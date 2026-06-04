#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/stl/filesystem.h>

#include <string>
#include <vector>
#include <cmath>
#include <filesystem>

#pragma comment(lib, "windowscodecs.lib")
#pragma comment(lib, "ole32.lib")

#include <wincodec.h>
#include <comdef.h>

namespace py = pybind11;

struct ThumbResult {
    bool success;
    std::string output_path;
    std::string error;
};

class ComInit {
public:
    ComInit() : hr_(CoInitializeEx(nullptr, COINIT_MULTITHREADED)) {}
    ~ComInit() { if (SUCCEEDED(hr_)) CoUninitialize(); }
    bool ok() const { return SUCCEEDED(hr_); }
private:
    HRESULT hr_;
};

static std::string wide_to_utf8(const wchar_t* wstr) {
    if (!wstr) return {};
    int len = WideCharToMultiByte(CP_UTF8, 0, wstr, -1, nullptr, 0, nullptr, nullptr);
    if (len <= 0) return {};
    std::string result(len - 1, '\0');
    WideCharToMultiByte(CP_UTF8, 0, wstr, -1, result.data(), len, nullptr, nullptr);
    return result;
}

static std::wstring utf8_to_wide(const std::string& str) {
    if (str.empty()) return {};
    int len = MultiByteToWideChar(CP_UTF8, 0, str.c_str(), -1, nullptr, 0);
    if (len <= 0) return {};
    std::wstring result(len - 1, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, str.c_str(), -1, result.data(), len);
    return result;
}

static HRESULT create_wic_factory(IWICImagingFactory** ppFactory) {
    return CoCreateInstance(
        CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(ppFactory));
}

static UINT get_exif_orientation(IWICBitmapFrameDecode* pFrame) {
    UINT orientation = 1;
    IWICMetadataQueryReader* pQuery = nullptr;
    HRESULT hr = pFrame->GetMetadataQueryReader(&pQuery);
    if (SUCCEEDED(hr) && pQuery) {
        PROPVARIANT prop;
        PropVariantInit(&prop);
        hr = pQuery->GetMetadataByName(L"System.Photo.Orientation", &prop);
        if (SUCCEEDED(hr) && prop.vt == VT_UI2) {
            orientation = prop.uiVal;
        }
        PropVariantClear(&prop);
        // 尝试 EXIF 路径
        if (orientation == 1) {
            hr = pQuery->GetMetadataByName(L"/app1/ifd/{ushort=274}", &prop);
            if (SUCCEEDED(hr) && prop.vt == VT_UI2) {
                orientation = prop.uiVal;
            }
            PropVariantClear(&prop);
        }
        pQuery->Release();
    }
    return orientation;
}

static HRESULT apply_exif_rotation(IWICImagingFactory* pFactory,
                                    IWICBitmapSource* pSource,
                                    UINT orientation,
                                    IWICBitmapSource** ppResult) {
    if (orientation <= 1 || orientation > 8) {
        *ppResult = pSource;
        pSource->AddRef();
        return S_OK;
    }

    IWICBitmapFlipRotator* pRotator = nullptr;
    HRESULT hr = pFactory->CreateBitmapFlipRotator(&pRotator);
    if (FAILED(hr)) {
        *ppResult = pSource;
        pSource->AddRef();
        return hr;
    }

    WICBitmapTransformOptions options = WICBitmapTransformRotate0;
    switch (orientation) {
        case 2: options = WICBitmapTransformFlipHorizontal; break;
        case 3: options = WICBitmapTransformRotate180; break;
        case 4: options = WICBitmapTransformFlipVertical; break;
        case 5: options = (WICBitmapTransformOptions)(
            WICBitmapTransformRotate270 | WICBitmapTransformFlipHorizontal); break;
        case 6: options = WICBitmapTransformRotate90; break;
        case 7: options = (WICBitmapTransformOptions)(
            WICBitmapTransformRotate90 | WICBitmapTransformFlipHorizontal); break;
        case 8: options = WICBitmapTransformRotate270; break;
    }

    hr = pRotator->Initialize(pSource, options);
    if (FAILED(hr)) {
        pRotator->Release();
        *ppResult = pSource;
        pSource->AddRef();
        return hr;
    }

    *ppResult = pRotator;
    return S_OK;
}

static ThumbResult generate_single_thumbnail(
    IWICImagingFactory* pFactory,
    const std::string& image_path_utf8,
    const std::string& output_dir_utf8,
    int max_edge,
    int jpeg_quality)
{
    ThumbResult result;
    result.success = false;

    std::wstring wImagePath = utf8_to_wide(image_path_utf8);

    // 检查源文件存在
    if (!std::filesystem::exists(wImagePath)) {
        result.error = "source file not found";
        return result;
    }

    // 解码
    IWICBitmapDecoder* pDecoder = nullptr;
    HRESULT hr = pFactory->CreateDecoderFromFilename(
        wImagePath.c_str(), nullptr, GENERIC_READ,
        WICDecodeMetadataCacheOnDemand, &pDecoder);
    if (FAILED(hr)) {
        result.error = "WIC decode failed: " + std::to_string(hr);
        return result;
    }

    IWICBitmapFrameDecode* pFrame = nullptr;
    hr = pDecoder->GetFrame(0, &pFrame);
    if (FAILED(hr)) {
        pDecoder->Release();
        result.error = "WIC get frame failed: " + std::to_string(hr);
        return result;
    }

    // 获取原始尺寸
    UINT origW = 0, origH = 0;
    pFrame->GetSize(&origW, &origH);

    // EXIF 旋转
    UINT orientation = get_exif_orientation(pFrame);
    IWICBitmapSource* pOriented = nullptr;
    apply_exif_rotation(pFactory, pFrame, orientation, &pOriented);
    pFrame->Release();

    // 旋转后可能交换宽高
    UINT srcW = origW, srcH = origH;
    if (orientation == 5 || orientation == 6 || orientation == 7 || orientation == 8) {
        std::swap(srcW, srcH);
    }

    // 计算目标尺寸（保持宽高比）
    UINT thumbW = srcW, thumbH = srcH;
    if (srcW > max_edge || srcH > max_edge) {
        double scale = static_cast<double>(max_edge) / std::max(srcW, srcH);
        thumbW = static_cast<UINT>(std::round(srcW * scale));
        thumbH = static_cast<UINT>(std::round(srcH * scale));
        if (thumbW < 1) thumbW = 1;
        if (thumbH < 1) thumbH = 1;
    }

    // 缩放
    IWICBitmapScaler* pScaler = nullptr;
    hr = pFactory->CreateBitmapScaler(&pScaler);
    if (FAILED(hr)) {
        pOriented->Release();
        pDecoder->Release();
        result.error = "WIC create scaler failed: " + std::to_string(hr);
        return result;
    }

    hr = pScaler->Initialize(pOriented, thumbW, thumbH,
        (thumbW < srcW / 2) ? WICBitmapInterpolationModeFant : WICBitmapInterpolationModeHighQualityCubic);
    pOriented->Release();
    if (FAILED(hr)) {
        pScaler->Release();
        pDecoder->Release();
        result.error = "WIC scale failed: " + std::to_string(hr);
        return result;
    }

    // 构造输出路径（使用 SHA256 命名规则由 Python 侧计算，此处直接接收输出路径）
    // 生成输出文件名：与 Python 侧 cache_path_for() 一致
    // 但为了简化，Python 侧会传入完整的输出路径
    // 这里我们改为：接收 output_path 而非 output_dir
    // 实际上，为了与 Python 侧的 SHA256 命名保持一致，
    // Python 侧会计算好输出路径并传入

    // 编码为 JPEG
    std::wstring wOutputDir = utf8_to_wide(output_dir_utf8);

    // 计算输出文件名（与 Python 侧一致的 SHA256 命名）
    // Python 侧会提供完整的输出路径，我们改为接收 output_path
    // 但当前接口是 output_dir，需要在这里计算文件名
    // 为保持兼容，我们使用 Python 侧传入的完整输出路径

    // 实际上，我们修改接口：Python 侧传入 (image_path, output_path) 对
    // 但为了保持 batch 接口简洁，我们让 Python 侧传入 output_paths

    // 暂时使用简化方案：输出路径 = output_dir + "\\" + 原文件名 + ".jpg"
    // 但这与 Python 侧的 SHA256 命名不一致
    // 最终方案：Python 侧传入每个图片对应的输出路径

    pScaler->Release();
    pDecoder->Release();

    // 此函数不应被直接调用，使用下面的 batch 函数
    result.error = "internal: use thumb_generate_batch_v2";
    return result;
}

// 实际使用的批量生成函数：接收 (image_path, output_path) 对
struct ThumbInput {
    std::string image_path;
    std::string output_path;
};

static ThumbResult generate_one(
    IWICImagingFactory* pFactory,
    const ThumbInput& input,
    int max_edge,
    int jpeg_quality)
{
    ThumbResult result;
    result.success = false;

    std::wstring wSrcPath = utf8_to_wide(input.image_path);
    std::wstring wDstPath = utf8_to_wide(input.output_path);

    // 检查输出目录存在
    std::filesystem::path dstDir = std::filesystem::path(wDstPath).parent_path();
    if (!std::filesystem::exists(dstDir)) {
        std::filesystem::create_directories(dstDir);
    }

    // 解码
    IWICBitmapDecoder* pDecoder = nullptr;
    HRESULT hr = pFactory->CreateDecoderFromFilename(
        wSrcPath.c_str(), nullptr, GENERIC_READ,
        WICDecodeMetadataCacheOnDemand, &pDecoder);
    if (FAILED(hr)) {
        result.error = "decode failed: 0x" + std::to_string(hr);
        return result;
    }

    IWICBitmapFrameDecode* pFrame = nullptr;
    hr = pDecoder->GetFrame(0, &pFrame);
    if (FAILED(hr)) {
        pDecoder->Release();
        result.error = "get frame failed: 0x" + std::to_string(hr);
        return result;
    }

    // 获取原始尺寸
    UINT origW = 0, origH = 0;
    pFrame->GetSize(&origW, &origH);

    // EXIF 旋转
    UINT orientation = get_exif_orientation(pFrame);
    IWICBitmapSource* pOriented = nullptr;
    apply_exif_rotation(pFactory, pFrame, orientation, &pOriented);
    pFrame->Release();

    // 旋转后可能交换宽高
    UINT srcW = origW, srcH = origH;
    if (orientation == 5 || orientation == 6 || orientation == 7 || orientation == 8) {
        std::swap(srcW, srcH);
    }

    // 计算目标尺寸
    UINT thumbW = srcW, thumbH = srcH;
    if (srcW > static_cast<UINT>(max_edge) || srcH > static_cast<UINT>(max_edge)) {
        double scale = static_cast<double>(max_edge) / std::max(srcW, srcH);
        thumbW = static_cast<UINT>(std::round(srcW * scale));
        thumbH = static_cast<UINT>(std::round(srcH * scale));
        if (thumbW < 1) thumbW = 1;
        if (thumbH < 1) thumbH = 1;
    }

    // 缩放
    IWICBitmapScaler* pScaler = nullptr;
    hr = pFactory->CreateBitmapScaler(&pScaler);
    if (FAILED(hr)) {
        pOriented->Release();
        pDecoder->Release();
        result.error = "create scaler failed: 0x" + std::to_string(hr);
        return result;
    }

    // 大幅缩小时用 Fant 算法（高质量），轻微缩放用立方插值
    WICBitmapInterpolationMode interpMode =
        (thumbW * 2 < srcW) ? WICBitmapInterpolationModeFant
                            : WICBitmapInterpolationModeHighQualityCubic;
    hr = pScaler->Initialize(pOriented, thumbW, thumbH, interpMode);
    pOriented->Release();
    if (FAILED(hr)) {
        pScaler->Release();
        pDecoder->Release();
        result.error = "scale failed: 0x" + std::to_string(hr);
        return result;
    }

    // 编码为 JPEG
    IWICBitmapEncoder* pEncoder = nullptr;
    hr = pFactory->CreateEncoder(GUID_ContainerFormatJpeg, nullptr, &pEncoder);
    if (FAILED(hr)) {
        pScaler->Release();
        pDecoder->Release();
        result.error = "create encoder failed: 0x" + std::to_string(hr);
        return result;
    }

    IStream* pStream = nullptr;
    hr = SHCreateStreamOnFileEx(
        wDstPath.c_str(), STGM_CREATE | STGM_WRITE, 0, FALSE, nullptr, &pStream);
    if (FAILED(hr)) {
        pEncoder->Release();
        pScaler->Release();
        pDecoder->Release();
        result.error = "create stream failed: 0x" + std::to_string(hr);
        return result;
    }

    hr = pEncoder->Initialize(pStream, WICBitmapEncoderNoCache);
    if (FAILED(hr)) {
        pStream->Release();
        pEncoder->Release();
        pScaler->Release();
        pDecoder->Release();
        result.error = "encoder init failed: 0x" + std::to_string(hr);
        return result;
    }

    IWICBitmapFrameEncode* pFrameEncode = nullptr;
    IPropertyBag2* pProps = nullptr;
    hr = pEncoder->CreateNewFrame(&pFrameEncode, &pProps);
    if (FAILED(hr)) {
        pStream->Release();
        pEncoder->Release();
        pScaler->Release();
        pDecoder->Release();
        result.error = "create frame encode failed: 0x" + std::to_string(hr);
        return result;
    }

    // 设置 JPEG 质量
    PROPBAG2 bag = {};
    bag.pstrName = const_cast<LPOLESTR>(L"ImageQuality");
    VARIANT var;
    VariantInit(&var);
    var.vt = VT_R4;
    var.fltVal = static_cast<float>(jpeg_quality) / 100.0f;
    pProps->Write(1, &bag, &var);
    VariantClear(&var);
    pProps->Release();

    hr = pFrameEncode->Initialize(pProps);
    if (FAILED(hr)) {
        pFrameEncode->Release();
        pStream->Release();
        pEncoder->Release();
        pScaler->Release();
        pDecoder->Release();
        result.error = "frame encode init failed: 0x" + std::to_string(hr);
        return result;
    }

    hr = pFrameEncode->SetSize(thumbW, thumbH);
    if (FAILED(hr)) {
        pFrameEncode->Release();
        pStream->Release();
        pEncoder->Release();
        pScaler->Release();
        pDecoder->Release();
        result.error = "set size failed: 0x" + std::to_string(hr);
        return result;
    }

    // 设置像素格式为 24bpp RGB
    WICPixelFormatGUID pixelFormat = GUID_WICPixelFormat24bppRGB;
    hr = pFrameEncode->SetPixelFormat(&pixelFormat);
    if (FAILED(hr)) {
        pFrameEncode->Release();
        pStream->Release();
        pEncoder->Release();
        pScaler->Release();
        pDecoder->Release();
        result.error = "set pixel format failed: 0x" + std::to_string(hr);
        return result;
    }

    // 如果源是 RGBA，WIC 会自动转换为 RGB（丢弃 alpha 或合成白色背景）
    // 需要使用 FormatConverter 确保像素格式匹配
    IWICFormatConverter* pConverter = nullptr;
    hr = pFactory->CreateFormatConverter(&pConverter);
    if (FAILED(hr)) {
        pFrameEncode->Release();
        pStream->Release();
        pEncoder->Release();
        pScaler->Release();
        pDecoder->Release();
        result.error = "create converter failed: 0x" + std::to_string(hr);
        return result;
    }

    // 检测源的像素格式，如果是 RGBA 则转换为 RGB（白色背景）
    WICPixelFormatGUID srcFormat;
    pScaler->GetPixelFormat(&srcFormat);

    BOOL canConvert = FALSE;
    hr = pConverter->CanConvert(srcFormat, pixelFormat, &canConvert);
    if (SUCCEEDED(hr) && canConvert) {
        hr = pConverter->Initialize(pScaler, pixelFormat,
            WICBitmapDitherTypeNone, nullptr, 0.0f,
            WICBitmapPaletteTypeCustom);
    } else {
        // 无法直接转换，尝试 32bppBGRA 作为中间格式
        WICPixelFormatGUID bgraFormat = GUID_WICPixelFormat32bppBGRA;
        hr = pConverter->CanConvert(srcFormat, bgraFormat, &canConvert);
        if (SUCCEEDED(hr) && canConvert) {
            IWICFormatConverter* pConverter2 = nullptr;
            hr = pFactory->CreateFormatConverter(&pConverter2);
            if (SUCCEEDED(hr)) {
                hr = pConverter2->Initialize(pScaler, bgraFormat,
                    WICBitmapDitherTypeNone, nullptr, 0.0f,
                    WICBitmapPaletteTypeCustom);
                if (SUCCEEDED(hr)) {
                    IWICFormatConverter* pConverter3 = nullptr;
                    hr = pFactory->CreateFormatConverter(&pConverter3);
                    if (SUCCEEDED(hr)) {
                        hr = pConverter3->Initialize(pConverter2, pixelFormat,
                            WICBitmapDitherTypeNone, nullptr, 0.0f,
                            WICBitmapPaletteTypeCustom);
                        if (SUCCEEDED(hr)) {
                            pConverter->Release();
                            pConverter = pConverter3;
                            pConverter2->Release();
                        } else {
                            pConverter3->Release();
                            pConverter2->Release();
                        }
                    } else {
                        pConverter2->Release();
                    }
                } else {
                    pConverter2->Release();
                }
            }
        }
    }

    if (FAILED(hr)) {
        pConverter->Release();
        pFrameEncode->Release();
        pStream->Release();
        pEncoder->Release();
        pScaler->Release();
        pDecoder->Release();
        result.error = "format convert failed: 0x" + std::to_string(hr);
        return result;
    }

    hr = pFrameEncode->WriteSource(pConverter, nullptr);
    pConverter->Release();
    if (FAILED(hr)) {
        pFrameEncode->Release();
        pStream->Release();
        pEncoder->Release();
        pScaler->Release();
        pDecoder->Release();
        result.error = "write source failed: 0x" + std::to_string(hr);
        return result;
    }

    hr = pFrameEncode->Commit();
    pFrameEncode->Release();
    if (FAILED(hr)) {
        pStream->Release();
        pEncoder->Release();
        pScaler->Release();
        pDecoder->Release();
        result.error = "frame commit failed: 0x" + std::to_string(hr);
        return result;
    }

    hr = pEncoder->Commit();
    pStream->Release();
    pEncoder->Release();
    pScaler->Release();
    pDecoder->Release();
    if (FAILED(hr)) {
        result.error = "encoder commit failed: 0x" + std::to_string(hr);
        return result;
    }

    result.success = true;
    result.output_path = input.output_path;
    return result;
}

std::vector<ThumbResult> thumb_generate_batch(
    const std::vector<std::tuple<std::string, std::string>>& path_pairs,
    int max_edge,
    int jpeg_quality)
{
    ComInit com;
    std::vector<ThumbResult> results;
    if (!com.ok()) {
        for (size_t i = 0; i < path_pairs.size(); ++i) {
            results.push_back({false, "", "COM init failed"});
        }
        return results;
    }

    IWICImagingFactory* pFactory = nullptr;
    HRESULT hr = create_wic_factory(&pFactory);
    if (FAILED(hr)) {
        for (size_t i = 0; i < path_pairs.size(); ++i) {
            results.push_back({false, "", "WIC factory creation failed"});
        }
        return results;
    }

    for (const auto& [image_path, output_path] : path_pairs) {
        ThumbInput input{image_path, output_path};
        results.push_back(generate_one(pFactory, input, max_edge, jpeg_quality));
    }

    pFactory->Release();
    return results;
}

PYBIND11_MODULE(_thumbnail, m) {
    m.doc() = "WIC-based thumbnail generation module";

    py::class_<ThumbResult>(m, "ThumbResult")
        .def_readonly("success", &ThumbResult::success)
        .def_readonly("output_path", &ThumbResult::output_path)
        .def_readonly("error", &ThumbResult::error);

    m.def("thumb_generate_batch", &thumb_generate_batch,
        "Generate thumbnails for a batch of images using WIC",
        py::arg("path_pairs"),
        py::arg("max_edge") = 256,
        py::arg("jpeg_quality") = 85);
}

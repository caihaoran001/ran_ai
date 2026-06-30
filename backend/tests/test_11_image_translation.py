from app.services.ai_service.client import _conservative_image_description, _image_description_needs_fallback


def test_image_description_needs_fallback_for_uncertain_text() -> None:
    description = "用户上传了一张体重秤照片，屏幕数字看不清，置信度：低。"

    assert _image_description_needs_fallback(description) is True


def test_image_description_needs_fallback_for_scale_without_number() -> None:
    description = "用户上传了一张体重秤照片，屏幕区域可见但反光明显。"

    assert _image_description_needs_fallback(description) is True


def test_conservative_description_marks_uncertain_result() -> None:
    description = "用户上传了一张餐食照片，可见一份餐食，具体食材无法确认，置信度：低。"

    result = _conservative_image_description(description)

    assert "不要把该识别结果当作确定事实" in result
    assert "请用户确认关键数字或食物细节" in result

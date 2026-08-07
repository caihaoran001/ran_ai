"""打卡体重/腰围解析：用例全部取自线上真实出现过的填写方式。

看板上「28天平均腰围变化 9.1cm」「平均体重变化 +0.6kg」两个错数字的成因就在这里，
所以每条断言都对应一种真实污染，不是构造的边界值。
"""

from __future__ import annotations

import pytest

from app.services.body_metrics import (
    REASON_NO_NUMBER,
    REASON_OUT_OF_RANGE,
    REASON_UNIT_AMBIGUOUS,
    parse_waist_cm,
    parse_weight_kg,
)


class TestWeight:
    def test_plain_kilogram(self):
        result = parse_weight_kg("68.5")
        assert result.value == 68.5
        # 没写单位、也没有问卷基线可核对，纳入统计但要能被单独数出来
        assert result.unit_verified is False

    def test_middle_dot_is_a_decimal_point(self):
        """线上真实：68·3、67·9。旧解析只取到 68，凭空造出 0.3 公斤的变化。"""
        assert parse_weight_kg("68·3").value == 68.3
        assert parse_weight_kg("67·9").value == 67.9

    def test_explicit_jin_is_halved(self):
        result = parse_weight_kg("140.2斤")
        assert result.value == 70.1
        assert result.corrected_from == "jin"
        assert result.unit_verified is True

    def test_explicit_kilogram_wins_over_jin_char(self):
        assert parse_weight_kg("70.7kg").value == 70.7
        assert parse_weight_kg("65公斤").value == 65.0

    def test_bare_jin_recognized_by_questionnaire_baseline(self):
        """线上 case 29 全程填 151.6/150.8，其实是斤。

        这类污染符号正确、幅度错一倍，绝对值阈值抓不到，只能靠本人基线识别。
        """
        result = parse_weight_kg("151.6", baseline_kg=75.8)
        assert result.value == 75.8
        assert result.corrected_from == "jin"
        assert result.unit_verified is True

    def test_bare_kilogram_confirmed_by_baseline(self):
        result = parse_weight_kg("74.4", baseline_kg=75.8)
        assert result.value == 74.4
        assert result.corrected_from is None
        assert result.unit_verified is True

    def test_value_that_fits_neither_unit_is_dropped(self):
        """基线 75.8 的人填 110：不像公斤（掉/涨 34 公斤不可能），也不像斤（55 公斤）。"""
        result = parse_weight_kg("110", baseline_kg=75.8)
        assert result.value is None
        assert result.reason == REASON_UNIT_AMBIGUOUS

    @pytest.mark.parametrize("raw", ["本日未测", "没称", "未测", "出差", "演出未称", "早上出门急未称", "", None])
    def test_no_number_means_not_measured(self, raw):
        result = parse_weight_kg(raw)
        assert result.value is None
        assert result.reason == REASON_NO_NUMBER

    def test_zero_is_not_a_weight(self):
        result = parse_weight_kg("0")
        assert result.value is None
        assert result.reason == REASON_OUT_OF_RANGE


class TestWaist:
    def test_plain_centimeter(self):
        assert parse_waist_cm("90").value == 90.0

    def test_explicit_centimeter_units(self):
        assert parse_waist_cm("90cm").value == 90.0
        assert parse_waist_cm("70厘米").value == 70.0

    def test_zero_is_the_source_of_the_68cm_jump(self):
        """线上有人连填 12 天 0，被当成真实腰围后算出「从 0 长到 68 厘米」。"""
        result = parse_waist_cm("0")
        assert result.value is None
        assert result.reason == REASON_OUT_OF_RANGE

    def test_chi_cun_written_out(self):
        """线上真实：22.5（2尺2.5）。2 尺 2.5 寸 = 2.25 尺 = 75 厘米。"""
        result = parse_waist_cm("22.5（2尺2.5）")
        assert result.value == pytest.approx(75.0, abs=0.1)
        assert result.corrected_from == "chi"

    def test_bare_chi_cun_number(self):
        """24.5 指 2 尺 4 寸半 ≈ 81.7 厘米；成年人不可能腰围 24 厘米，所以按尺换算是安全的。"""
        result = parse_waist_cm("24.5")
        assert result.value == pytest.approx(81.67, abs=0.05)
        assert result.corrected_from == "chi"

    def test_bare_chi_cun_confirmed_by_baseline(self):
        result = parse_waist_cm("25.5", baseline_cm=85.0)
        assert result.value == pytest.approx(85.0, abs=0.1)
        assert result.unit_verified is True

    def test_takes_waist_not_hip_when_both_present(self):
        """线上真实：腰围104    肚(臀围)围114 / 104    114——取前一个才是腰围。"""
        assert parse_waist_cm("腰围104    肚(臀围)围114").value == 104.0
        assert parse_waist_cm("104    114").value == 104.0

    def test_meter_and_millimeter(self):
        assert parse_waist_cm("0.9m").value == 90.0
        assert parse_waist_cm("900mm").value == 90.0

    @pytest.mark.parametrize("raw", ["本日未测", "未测", "没量", "无", "外出没量", "未测量", ""])
    def test_no_number_means_not_measured(self, raw):
        assert parse_waist_cm(raw).reason == REASON_NO_NUMBER

    def test_free_text_feeling_is_not_a_measurement(self):
        """线上真实：「肚子小了点，裤子有松感」——是体感，不是围度数字。"""
        assert parse_waist_cm("肚子小了点，裤子有松感").reason == REASON_NO_NUMBER

    def test_value_that_fits_neither_unit_is_dropped(self):
        result = parse_waist_cm("300", baseline_cm=85.0)
        assert result.value is None
        assert result.reason == REASON_UNIT_AMBIGUOUS

package com.library.dto;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.math.BigDecimal;

@Data
public class BorrowRuleRequest {

    @NotNull(message = "请填写同时借阅上限")
    @Min(value = 1, message = "借阅上限至少为 1 本")
    @Max(value = 20, message = "借阅上限不能超过 20 本")
    private Integer maxBorrowCount;

    @NotNull(message = "请填写借期天数")
    @Min(value = 1, message = "借期至少 1 天")
    @Max(value = 180, message = "借期不能超过 180 天")
    private Integer borrowDays;

    @NotNull(message = "请填写最大续借次数")
    @Min(value = 0, message = "续借次数不能为负数")
    @Max(value = 5, message = "续借次数不能超过 5 次")
    private Integer maxRenewCount;

    @NotNull(message = "请填写每次续借天数")
    @Min(value = 1, message = "续借天数至少 1 天")
    @Max(value = 90, message = "续借天数不能超过 90 天")
    private Integer renewDays;

    @NotNull(message = "请填写每日罚金")
    @DecimalMin(value = "0", message = "罚金不能为负数")
    @Digits(integer = 8, fraction = 2, message = "罚金最多保留两位小数")
    private BigDecimal finePerDay;

    @NotNull(message = "请填写罚金上限")
    @DecimalMin(value = "0", message = "罚金上限不能为负数")
    @Digits(integer = 8, fraction = 2, message = "罚金上限最多保留两位小数")
    private BigDecimal fineMax;

    @NotNull(message = "请填写预约数量上限")
    @Min(value = 0, message = "预约上限不能为负数")
    @Max(value = 10, message = "预约上限不能超过 10 本")
    private Integer reserveLimit;

    @NotNull(message = "请填写预约保留天数")
    @Min(value = 1, message = "保留天数至少 1 天")
    @Max(value = 30, message = "保留天数不能超过 30 天")
    private Integer holdDays;
}

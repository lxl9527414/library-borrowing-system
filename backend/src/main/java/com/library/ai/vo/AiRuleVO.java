package com.library.ai.vo;

import com.library.entity.BorrowRule;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;

/**
 * AI 会话上下文中的借阅规则（契约 §2.1），只暴露 prompt 需要的字段。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class AiRuleVO {

    private Integer maxBorrowCount;
    private Integer borrowDays;
    private Integer maxRenewCount;
    private Integer renewDays;
    private BigDecimal finePerDay;
    private BigDecimal fineMax;
    private Integer reserveLimit;
    private Integer holdDays;

    public static AiRuleVO from(BorrowRule rule) {
        if (rule == null) {
            return null;
        }
        return new AiRuleVO(
                rule.getMaxBorrowCount(),
                rule.getBorrowDays(),
                rule.getMaxRenewCount(),
                rule.getRenewDays(),
                rule.getFinePerDay(),
                rule.getFineMax(),
                rule.getReserveLimit(),
                rule.getHoldDays());
    }
}

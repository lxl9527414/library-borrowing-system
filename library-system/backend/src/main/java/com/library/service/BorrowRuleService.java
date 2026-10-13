package com.library.service;

import com.library.dto.BorrowRuleRequest;
import com.library.entity.BorrowRule;
import com.library.mapper.BorrowRuleMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;

@Service
@RequiredArgsConstructor
public class BorrowRuleService {

    private final BorrowRuleMapper borrowRuleMapper;

    /**
     * 读取当前生效的借阅规则；配置缺失时按默认值落库，保证调用方永远拿到一份规则。
     */
    public BorrowRule current() {
        BorrowRule rule = borrowRuleMapper.selectById(BorrowRule.CONFIG_ID);
        if (rule != null) {
            return rule;
        }
        BorrowRule fallback = new BorrowRule();
        fallback.setId(BorrowRule.CONFIG_ID);
        fallback.setMaxBorrowCount(5);
        fallback.setBorrowDays(30);
        fallback.setMaxRenewCount(1);
        fallback.setRenewDays(15);
        fallback.setFinePerDay(new BigDecimal("0.20"));
        fallback.setFineMax(new BigDecimal("20.00"));
        fallback.setReserveLimit(3);
        fallback.setHoldDays(3);
        borrowRuleMapper.insert(fallback);
        return fallback;
    }

    @Transactional
    public BorrowRule update(BorrowRuleRequest request) {
        BorrowRule rule = current();
        rule.setMaxBorrowCount(request.getMaxBorrowCount());
        rule.setBorrowDays(request.getBorrowDays());
        rule.setMaxRenewCount(request.getMaxRenewCount());
        rule.setRenewDays(request.getRenewDays());
        rule.setFinePerDay(request.getFinePerDay());
        rule.setFineMax(request.getFineMax());
        rule.setReserveLimit(request.getReserveLimit());
        rule.setHoldDays(request.getHoldDays());
        borrowRuleMapper.updateById(rule);
        return rule;
    }
}

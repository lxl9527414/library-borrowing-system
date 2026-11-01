package com.library.service;

import com.library.dto.BorrowRuleRequest;
import com.library.entity.BorrowRule;
import com.library.mapper.BorrowRuleMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BorrowRuleServiceTest {

    @Mock
    private BorrowRuleMapper borrowRuleMapper;

    @InjectMocks
    private BorrowRuleService borrowRuleService;

    @Test
    @DisplayName("规则读取：配置存在时直接返回")
    void current_returnsExistingRule() {
        BorrowRule existing = new BorrowRule();
        existing.setId(BorrowRule.CONFIG_ID);
        existing.setMaxBorrowCount(8);
        when(borrowRuleMapper.selectById(BorrowRule.CONFIG_ID)).thenReturn(existing);

        BorrowRule rule = borrowRuleService.current();

        assertThat(rule.getMaxBorrowCount()).isEqualTo(8);
    }

    @Test
    @DisplayName("规则读取：配置缺失时落默认值，保证调用方拿到规则")
    void current_whenMissing_thenCreateDefaults() {
        when(borrowRuleMapper.selectById(BorrowRule.CONFIG_ID)).thenReturn(null);

        BorrowRule rule = borrowRuleService.current();

        assertThat(rule.getId()).isEqualTo(BorrowRule.CONFIG_ID);
        assertThat(rule.getMaxBorrowCount()).isEqualTo(5);
        assertThat(rule.getBorrowDays()).isEqualTo(30);
        assertThat(rule.getMaxRenewCount()).isEqualTo(1);
        assertThat(rule.getRenewDays()).isEqualTo(15);
        assertThat(rule.getFinePerDay()).isEqualByComparingTo("0.20");
        assertThat(rule.getFineMax()).isEqualByComparingTo("20.00");
        assertThat(rule.getReserveLimit()).isEqualTo(3);
        assertThat(rule.getHoldDays()).isEqualTo(3);
        verify(borrowRuleMapper).insert(rule);
    }

    @Test
    @DisplayName("规则更新：所有字段按请求写入")
    void update_appliesRequestFields() {
        BorrowRule existing = new BorrowRule();
        existing.setId(BorrowRule.CONFIG_ID);
        when(borrowRuleMapper.selectById(BorrowRule.CONFIG_ID)).thenReturn(existing);

        BorrowRuleRequest request = new BorrowRuleRequest();
        request.setMaxBorrowCount(6);
        request.setBorrowDays(45);
        request.setMaxRenewCount(2);
        request.setRenewDays(20);
        request.setFinePerDay(new BigDecimal("1.00"));
        request.setFineMax(new BigDecimal("30.00"));
        request.setReserveLimit(5);
        request.setHoldDays(7);

        BorrowRule updated = borrowRuleService.update(request);

        ArgumentCaptor<BorrowRule> captor = ArgumentCaptor.forClass(BorrowRule.class);
        verify(borrowRuleMapper).updateById(captor.capture());
        BorrowRule saved = captor.getValue();
        assertThat(saved.getMaxBorrowCount()).isEqualTo(6);
        assertThat(saved.getBorrowDays()).isEqualTo(45);
        assertThat(saved.getMaxRenewCount()).isEqualTo(2);
        assertThat(saved.getRenewDays()).isEqualTo(20);
        assertThat(saved.getFinePerDay()).isEqualByComparingTo("1.00");
        assertThat(saved.getFineMax()).isEqualByComparingTo("30.00");
        assertThat(saved.getReserveLimit()).isEqualTo(5);
        assertThat(saved.getHoldDays()).isEqualTo(7);
        assertThat(updated).isSameAs(saved);
    }
}

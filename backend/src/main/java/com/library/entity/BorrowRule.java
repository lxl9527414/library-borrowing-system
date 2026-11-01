package com.library.entity;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 借阅规则：单行配置表，id 固定为 1。
 */
@Data
@TableName("borrow_rule")
public class BorrowRule {

    public static final long CONFIG_ID = 1L;

    @TableId
    private Long id;
    private Integer maxBorrowCount;
    private Integer borrowDays;
    private Integer maxRenewCount;
    private Integer renewDays;
    private BigDecimal finePerDay;
    private BigDecimal fineMax;
    private Integer reserveLimit;
    private Integer holdDays;
    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createdAt;
    @TableField(fill = FieldFill.INSERT_UPDATE)
    private LocalDateTime updatedAt;
}

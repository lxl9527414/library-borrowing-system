package com.library.entity;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableLogic;
import com.baomidou.mybatisplus.annotation.TableName;
import com.fasterxml.jackson.annotation.JsonIgnore;
import lombok.Data;

import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@TableName("borrow_record")
public class BorrowRecord {

    @TableId
    private Long id;
    private String borrowNo;
    private Long userId;
    private Long bookId;
    private String bookTitle;
    private String bookIsbn;
    private String status;
    private LocalDateTime borrowedAt;
    private LocalDateTime dueAt;
    private LocalDateTime returnedAt;
    private Integer renewCount;
    private BigDecimal fineAmount;
    private String fineStatus;
    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createdAt;
    @TableField(fill = FieldFill.INSERT_UPDATE)
    private LocalDateTime updatedAt;
    @JsonIgnore
    @TableLogic
    private Integer deleted;
}

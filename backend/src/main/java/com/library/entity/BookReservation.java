package com.library.entity;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableLogic;
import com.baomidou.mybatisplus.annotation.TableName;
import com.fasterxml.jackson.annotation.JsonIgnore;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("book_reservation")
public class BookReservation {

    @TableId
    private Long id;
    private Long bookId;
    private Long userId;
    private String status;
    private LocalDateTime readyAt;
    private LocalDateTime expireAt;
    private LocalDateTime completedAt;
    private LocalDateTime canceledAt;
    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createdAt;
    @TableField(fill = FieldFill.INSERT_UPDATE)
    private LocalDateTime updatedAt;
    @JsonIgnore
    @TableLogic
    private Integer deleted;
}

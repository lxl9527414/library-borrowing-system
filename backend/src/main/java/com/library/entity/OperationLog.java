package com.library.entity;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("operation_log")
public class OperationLog {

    @TableId
    private Long id;
    private Long userId;
    private String username;
    private String module;
    private String action;
    private String method;
    private String uri;
    private String params;
    private String ip;
    private String status;
    private String errorMessage;
    private Long durationMs;
    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createdAt;
}

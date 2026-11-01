package com.library.ai.dto;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * AI 工具 return_book 的请求体：只接收借阅记录 ID。
 *
 * <p>「这条记录是不是自己的」由 {@link com.library.service.BorrowService#returnBook} 用
 * 当前登录用户校验，AI 工具层不接收 userId。
 */
@Data
public class AiReturnRequest {

    @NotNull(message = "请选择要归还的借阅记录")
    private Long borrowId;
}

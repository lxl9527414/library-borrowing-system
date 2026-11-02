package com.library.ai.controller;

import com.library.ai.dto.AiBorrowRequest;
import com.library.ai.dto.AiReserveRequest;
import com.library.ai.dto.AiReturnRequest;
import com.library.ai.service.AiToolService;
import com.library.ai.vo.AiBookDetailVO;
import com.library.ai.vo.AiBookVO;
import com.library.ai.vo.AiBorrowRecordVO;
import com.library.ai.vo.AiContextVO;
import com.library.ai.vo.AiRulesTextVO;
import com.library.common.ApiResponse;
import com.library.common.PageResult;
import com.library.entity.BookReservation;
import com.library.entity.BorrowRecord;
import com.library.log.Auditable;
import com.library.security.AuthContext;
import com.library.security.UserSession;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * AI 服务的内部工具接口（契约 §2），只允许 AI 服务经内网调用。
 *
 * <p>安全约束：
 * <ul>
 *     <li>调用方鉴权：{@code AiInternalTokenFilter} 校验 X-Internal-Token；</li>
 *     <li>用户鉴权：{@code AuthInterceptor} 解析 Authorization token 并写入请求属性，
 *         本类只通过 {@code @RequestAttribute} 读取，绝不从请求体接收 userId；</li>
 *     <li>写操作复用现有事务方法并记录 @Auditable 操作日志。</li>
 * </ul>
 */
@RestController
@RequestMapping("/api/ai/internal")
@RequiredArgsConstructor
public class AiToolController {

    private final AiToolService aiToolService;

    @GetMapping("/context")
    public ApiResponse<AiContextVO> context(
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(aiToolService.context(currentUser));
    }

    @GetMapping("/rules/text")
    public ApiResponse<AiRulesTextVO> rulesText() {
        return ApiResponse.ok(aiToolService.rulesText());
    }

    @GetMapping("/books")
    public ApiResponse<PageResult<AiBookVO>> books(
            @RequestParam(required = false) String keyword,
            @RequestParam(required = false) String category,
            @RequestParam(defaultValue = "1") long current,
            @RequestParam(defaultValue = "5") long size) {
        return ApiResponse.ok(aiToolService.searchBooks(keyword, category, current, size));
    }

    @GetMapping("/books/{id}")
    public ApiResponse<AiBookDetailVO> bookDetail(
            @PathVariable Long id,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(aiToolService.bookDetail(id, currentUser));
    }

    @GetMapping("/borrows/my")
    public ApiResponse<List<AiBorrowRecordVO>> myBorrows(
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(aiToolService.myBorrows(currentUser));
    }

    @PostMapping("/borrow")
    @Auditable(module = "AI工具", action = "AI借阅图书")
    public ApiResponse<BorrowRecord> borrow(
            @Valid @RequestBody AiBorrowRequest request,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(aiToolService.borrow(request.getBookId(), currentUser));
    }

    @PostMapping("/return")
    @Auditable(module = "AI工具", action = "AI归还图书")
    public ApiResponse<BorrowRecord> returnBook(
            @Valid @RequestBody AiReturnRequest request,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(aiToolService.returnBook(request.getBorrowId(), currentUser));
    }

    @PostMapping("/reserve")
    @Auditable(module = "AI工具", action = "AI预约图书")
    public ApiResponse<BookReservation> reserve(
            @Valid @RequestBody AiReserveRequest request,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(aiToolService.reserve(request.getBookId(), currentUser));
    }
}

package com.library.controller;

import com.library.common.ApiResponse;
import com.library.common.PageResult;
import com.library.dto.ReservationRequest;
import com.library.entity.BookReservation;
import com.library.log.Auditable;
import com.library.security.AuthContext;
import com.library.security.UserSession;
import com.library.service.ReservationService;
import com.library.vo.BookReservationVO;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/reservations")
@RequiredArgsConstructor
public class ReservationController {

    private final ReservationService reservationService;

    @GetMapping
    public ApiResponse<PageResult<BookReservationVO>> page(
            @RequestParam(defaultValue = "1") long current,
            @RequestParam(defaultValue = "10") long size,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String keyword,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(PageResult.of(reservationService.page(current, size, status, keyword, currentUser)));
    }

    @GetMapping("/active")
    public ApiResponse<List<BookReservation>> active(
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(reservationService.activeList(currentUser.getId()));
    }

    @PostMapping
    @Auditable(module = "预约", action = "预约图书")
    public ApiResponse<BookReservation> reserve(
            @Valid @RequestBody ReservationRequest request,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(reservationService.reserve(request.getBookId(), currentUser));
    }

    @DeleteMapping("/{id}")
    @Auditable(module = "预约", action = "取消预约")
    public ApiResponse<Void> cancel(
            @PathVariable Long id,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        reservationService.cancel(id, currentUser);
        return ApiResponse.ok();
    }
}

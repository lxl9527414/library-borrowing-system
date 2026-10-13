package com.library.controller;

import com.library.common.ApiResponse;
import com.library.dto.BorrowRuleRequest;
import com.library.entity.BorrowRule;
import com.library.log.Auditable;
import com.library.security.RequireRole;
import com.library.service.BorrowRuleService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/rules")
@RequiredArgsConstructor
public class BorrowRuleController {

    private final BorrowRuleService borrowRuleService;

    @GetMapping
    public ApiResponse<BorrowRule> current() {
        return ApiResponse.ok(borrowRuleService.current());
    }

    @RequireRole("ADMIN")
    @PutMapping
    @Auditable(module = "借阅规则", action = "修改借阅规则")
    public ApiResponse<BorrowRule> update(@Valid @RequestBody BorrowRuleRequest request) {
        return ApiResponse.ok(borrowRuleService.update(request));
    }
}

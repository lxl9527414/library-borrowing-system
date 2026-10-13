package com.library.controller;

import com.library.common.ApiResponse;
import com.library.common.PageResult;
import com.library.entity.OperationLog;
import com.library.security.RequireRole;
import com.library.service.OperationLogService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/logs")
@RequireRole("ADMIN")
@RequiredArgsConstructor
public class OperationLogController {

    private final OperationLogService operationLogService;

    @GetMapping
    public ApiResponse<PageResult<OperationLog>> page(
            @RequestParam(defaultValue = "1") long current,
            @RequestParam(defaultValue = "10") long size,
            @RequestParam(required = false) String module,
            @RequestParam(required = false) String keyword) {
        return ApiResponse.ok(PageResult.of(operationLogService.page(current, size, module, keyword)));
    }

    @GetMapping("/modules")
    public ApiResponse<List<String>> modules() {
        return ApiResponse.ok(operationLogService.modules());
    }
}

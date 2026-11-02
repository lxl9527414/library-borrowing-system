package com.library.service;

import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.library.entity.OperationLog;
import com.library.mapper.OperationLogMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.util.List;

@Service
@RequiredArgsConstructor
public class OperationLogService {

    private final OperationLogMapper operationLogMapper;

    public IPage<OperationLog> page(long current, long size, String module, String keyword) {
        return operationLogMapper.selectPage(new Page<>(current, size), Wrappers.<OperationLog>lambdaQuery()
                .eq(StringUtils.hasText(module), OperationLog::getModule, module)
                .and(StringUtils.hasText(keyword), wrapper -> wrapper
                        .like(OperationLog::getAction, keyword)
                        .or()
                        .like(OperationLog::getUsername, keyword)
                        .or()
                        .like(OperationLog::getUri, keyword))
                .orderByDesc(OperationLog::getCreatedAt)
                .orderByDesc(OperationLog::getId));
    }

    public List<String> modules() {
        return operationLogMapper.selectObjs(Wrappers.<OperationLog>lambdaQuery()
                        .select(OperationLog::getModule)
                        .groupBy(OperationLog::getModule)
                        .orderByAsc(OperationLog::getModule))
                .stream()
                .map(String::valueOf)
                .toList();
    }

    public int cleanup(int retentionDays) {
        return operationLogMapper.delete(Wrappers.<OperationLog>lambdaQuery()
                .lt(OperationLog::getCreatedAt, LocalDateTime.now().minusDays(retentionDays)));
    }
}

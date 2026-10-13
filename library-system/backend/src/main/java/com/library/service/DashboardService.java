package com.library.service;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.library.entity.Book;
import com.library.entity.BookReservation;
import com.library.entity.BorrowRecord;
import com.library.entity.Role;
import com.library.entity.User;
import com.library.common.ReservationStatus;
import com.library.mapper.BookMapper;
import com.library.mapper.BookReservationMapper;
import com.library.mapper.BorrowRecordMapper;
import com.library.mapper.UserMapper;
import com.library.security.UserSession;
import com.library.vo.BorrowRecordVO;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class DashboardService {

    private final BookMapper bookMapper;
    private final BorrowRecordMapper borrowRecordMapper;
    private final UserMapper userMapper;
    private final BookReservationMapper bookReservationMapper;

    public Map<String, Object> overview(UserSession currentUser) {
        LocalDateTime now = LocalDateTime.now();
        Long userId = Role.ADMIN.name().equals(currentUser.getRole()) ? null : currentUser.getId();
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("bookCount", bookMapper.selectCount(null));
        data.put("availableCount", sum(bookMapper.selectObjs(
                Wrappers.<Book>query().select("COALESCE(SUM(available_stock), 0)"))));
        data.put("borrowedCount", borrowRecordMapper.selectCount(Wrappers.<BorrowRecord>lambdaQuery()
                .eq(BorrowRecord::getStatus, "BORROWED")
                .eq(userId != null, BorrowRecord::getUserId, userId)));
        data.put("overdueCount", borrowRecordMapper.selectCount(Wrappers.<BorrowRecord>lambdaQuery()
                .eq(BorrowRecord::getStatus, "BORROWED")
                .eq(userId != null, BorrowRecord::getUserId, userId)
                .lt(BorrowRecord::getDueAt, now)));
        data.put("readerCount", userMapper.selectCount(Wrappers.<User>lambdaQuery()
                .eq(User::getRole, Role.READER.name())
                .eq(User::getStatus, 1)));
        data.put("returnedCount", borrowRecordMapper.selectCount(Wrappers.<BorrowRecord>lambdaQuery()
                .eq(BorrowRecord::getStatus, "RETURNED")
                .eq(userId != null, BorrowRecord::getUserId, userId)));
        data.put("reservationCount", bookReservationMapper.selectCount(Wrappers.<BookReservation>lambdaQuery()
                .in(BookReservation::getStatus, ReservationStatus.ACTIVE)
                .eq(userId != null, BookReservation::getUserId, userId)));

        IPage<BorrowRecordVO> recentPage = borrowRecordMapper.selectRecordPage(
                new Page<>(1, 6), userId, null, null);
        List<BorrowRecordVO> records = recentPage.getRecords();
        records.forEach(record -> record.setOverdue(
                "BORROWED".equals(record.getStatus()) && record.getDueAt().isBefore(now)));
        data.put("recentRecords", records);
        return data;
    }

    private long sum(List<Object> values) {
        if (values == null || values.isEmpty() || values.get(0) == null) {
            return 0L;
        }
        return new BigDecimal(String.valueOf(values.get(0))).longValue();
    }
}

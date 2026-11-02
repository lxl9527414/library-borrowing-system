package com.library.ai.service;

import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.library.ai.vo.AiBookDetailVO;
import com.library.ai.vo.AiBookVO;
import com.library.ai.vo.AiBorrowRecordVO;
import com.library.ai.vo.AiContextVO;
import com.library.ai.vo.AiReservationVO;
import com.library.ai.vo.AiRuleVO;
import com.library.ai.vo.AiRulesTextVO;
import com.library.common.BusinessException;
import com.library.common.PageResult;
import com.library.common.ReservationStatus;
import com.library.entity.Book;
import com.library.entity.BookReservation;
import com.library.entity.BorrowRecord;
import com.library.entity.BorrowRule;
import com.library.mapper.BookMapper;
import com.library.mapper.BookReservationMapper;
import com.library.mapper.BorrowRecordMapper;
import com.library.security.UserSession;
import com.library.service.BookService;
import com.library.service.BorrowRuleService;
import com.library.service.BorrowService;
import com.library.service.ReservationService;
import com.library.vo.BorrowRecordVO;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

/**
 * AI 内部工具的业务编排层（契约 §2）。
 *
 * <p>核心原则：
 * <ul>
 *     <li>只读工具复用现有 Service 的查询能力，不复制业务规则；</li>
 *     <li>写工具直接委托现有 {@link BorrowService}/{@link ReservationService} 的
 *         {@code @Transactional} 方法，事务、库存与规则校验保持单一来源；</li>
 *     <li>用户身份一律来自调用方传入的 {@link UserSession}（由 AuthInterceptor 从
 *         Authorization token 解析），本层不接收任何 userId 参数。</li>
 * </ul>
 */
@Service
@RequiredArgsConstructor
public class AiToolService {

    private static final long DEFAULT_BOOK_PAGE_SIZE = 5L;
    private static final long MAX_BOOK_PAGE_SIZE = 20L;
    private static final long MY_BORROW_PAGE_SIZE = 20L;
    private static final String STATUS_BORROWED = "BORROWED";

    private final BookService bookService;
    private final BookMapper bookMapper;
    private final BorrowService borrowService;
    private final BorrowRecordMapper borrowRecordMapper;
    private final ReservationService reservationService;
    private final BorrowRuleService borrowRuleService;
    private final BookReservationMapper bookReservationMapper;

    /**
     * 会话上下文：用户信息 + 当前借阅规则 + 4 个个人统计。
     *
     * <p>统计严格按当前登录用户维度查询（3 次固定查询，无 N+1），
     * 避免复用分页接口 ADMIN 的全局语义导致拿到他人数据。
     */
    public AiContextVO context(UserSession currentUser) {
        BorrowRule rule = borrowRuleService.current();
        Long userId = currentUser.getId();
        long borrowedCount = borrowRecordMapper.selectCount(Wrappers.<BorrowRecord>lambdaQuery()
                .eq(BorrowRecord::getUserId, userId)
                .eq(BorrowRecord::getStatus, STATUS_BORROWED));
        long overdueCount = borrowRecordMapper.selectCount(Wrappers.<BorrowRecord>lambdaQuery()
                .eq(BorrowRecord::getUserId, userId)
                .eq(BorrowRecord::getStatus, STATUS_BORROWED)
                .lt(BorrowRecord::getDueAt, LocalDateTime.now()));
        List<BookReservation> activeReservations = reservationService.activeList(userId);
        long pendingPickupCount = activeReservations.stream()
                .filter(reservation -> ReservationStatus.READY.equals(reservation.getStatus()))
                .count();
        return new AiContextVO(
                currentUser,
                AiRuleVO.from(rule),
                borrowedCount,
                overdueCount,
                activeReservations.size(),
                pendingPickupCount);
    }

    /**
     * 借阅规则自然语言化，作为 system prompt 的补充说明直接注入。
     */
    public AiRulesTextVO rulesText() {
        BorrowRule rule = borrowRuleService.current();
        String text = "每人最多同时借阅 " + text(rule.getMaxBorrowCount()) + " 本，"
                + "借期 " + text(rule.getBorrowDays()) + " 天，"
                + "最多续借 " + text(rule.getMaxRenewCount()) + " 次（每次 " + text(rule.getRenewDays()) + " 天），"
                + "逾期每天罚金 " + money(rule.getFinePerDay()) + " 元，"
                + "单笔封顶 " + money(rule.getFineMax()) + " 元，"
                + "最多可同时预约 " + text(rule.getReserveLimit()) + " 本图书，"
                + "待取书保留 " + text(rule.getHoldDays()) + " 天。";
        return new AiRulesTextVO(text);
    }

    /**
     * 检索图书：复用 {@link BookService#page}，分页大小收敛到契约要求的 1~20。
     */
    public PageResult<AiBookVO> searchBooks(String keyword, String category, long current, long size) {
        long pageCurrent = current <= 0 ? 1 : current;
        long pageSize = normalizeBookPageSize(size);
        IPage<Book> page = bookService.page(pageCurrent, pageSize, keyword, category);
        List<AiBookVO> records = page.getRecords().stream().map(AiBookVO::from).toList();
        return new PageResult<>(records, page.getTotal(), page.getCurrent(), page.getSize());
    }

    /**
     * 图书详情：库存信息 + 预约队列长度 + 当前用户在该书上的有效预约。
     *
     * <p>ReservationService 没有「按 bookId 统计队列」的公开方法，且本次改造不允许修改
     * 现有业务类，因此队列长度用其底层 mapper 做一次 count（READY+WAITING），
     * 不复用分页接口（该接口对读者只返回自己的预约，无法得到队列长度）。
     */
    public AiBookDetailVO bookDetail(Long bookId, UserSession currentUser) {
        Book book = bookMapper.selectById(bookId);
        if (book == null) {
            throw new BusinessException("图书不存在");
        }
        long waitingCount = bookReservationMapper.selectCount(Wrappers.<BookReservation>lambdaQuery()
                .eq(BookReservation::getBookId, bookId)
                .in(BookReservation::getStatus, ReservationStatus.ACTIVE));
        BookReservation myReservation = reservationService.activeList(currentUser.getId()).stream()
                .filter(reservation -> bookId.equals(reservation.getBookId()))
                .findFirst()
                .orElse(null);
        return new AiBookDetailVO(AiBookVO.from(book), waitingCount, AiReservationVO.from(myReservation));
    }

    /**
     * 我的在借记录：分页查询直接复用 {@link BorrowService#page}，
     * 其返回的 VO 已带 overdue 标记与数据库中的罚金字段，这里不再重算。
     */
    public List<AiBorrowRecordVO> myBorrows(UserSession currentUser) {
        IPage<BorrowRecordVO> page = borrowService.page(1, MY_BORROW_PAGE_SIZE, STATUS_BORROWED, null, currentUser);
        return page.getRecords().stream().map(AiBorrowRecordVO::from).toList();
    }

    /**
     * 借书：完全委托现有事务方法，AI 工具层不复制库存/上限/预约校验。
     */
    public BorrowRecord borrow(Long bookId, UserSession currentUser) {
        return borrowService.borrow(bookId, currentUser);
    }

    /**
     * 还书：完全委托现有事务方法，身份由 currentUser 决定（读者只能还自己的记录）。
     */
    public BorrowRecord returnBook(Long borrowId, UserSession currentUser) {
        return borrowService.returnBook(borrowId, currentUser);
    }

    /**
     * 预约：完全委托现有事务方法，AI 工具层不复制预约规则校验。
     */
    public BookReservation reserve(Long bookId, UserSession currentUser) {
        return reservationService.reserve(bookId, currentUser);
    }

    private long normalizeBookPageSize(long size) {
        if (size <= 0) {
            return DEFAULT_BOOK_PAGE_SIZE;
        }
        return Math.min(size, MAX_BOOK_PAGE_SIZE);
    }

    private String text(Integer value) {
        return value == null ? "-" : String.valueOf(value);
    }

    private String money(BigDecimal value) {
        return value == null ? "-" : value.toPlainString();
    }
}

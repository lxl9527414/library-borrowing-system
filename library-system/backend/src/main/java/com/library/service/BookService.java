package com.library.service;

import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.library.common.BusinessException;
import com.library.dto.BookSaveRequest;
import com.library.entity.Book;
import com.library.entity.BorrowRecord;
import com.library.mapper.BookMapper;
import com.library.mapper.BorrowRecordMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.util.List;

@Service
@RequiredArgsConstructor
public class BookService {

    private final BookMapper bookMapper;
    private final BorrowRecordMapper borrowRecordMapper;

    public IPage<Book> page(long current, long size, String keyword, String category) {
        return bookMapper.selectPage(
                new Page<>(current, size),
                Wrappers.<Book>lambdaQuery()
                        .and(StringUtils.hasText(keyword), wrapper -> wrapper
                                .like(Book::getTitle, keyword)
                                .or()
                                .like(Book::getAuthor, keyword)
                                .or()
                                .like(Book::getIsbn, keyword))
                        .eq(StringUtils.hasText(category), Book::getCategory, category)
                        .orderByDesc(Book::getUpdatedAt)
                        .orderByDesc(Book::getId)
        );
    }

    public List<String> categories() {
        return bookMapper.selectObjs(Wrappers.<Book>lambdaQuery()
                        .select(Book::getCategory)
                        .isNotNull(Book::getCategory)
                        .ne(Book::getCategory, "")
                        .groupBy(Book::getCategory)
                        .orderByAsc(Book::getCategory))
                .stream()
                .map(String::valueOf)
                .toList();
    }

    @Transactional
    public Book create(BookSaveRequest request) {
        assertIsbnAvailable(request.getIsbn().trim(), null);
        Book book = new Book();
        applyRequest(book, request);
        book.setAvailableStock(request.getTotalStock());
        bookMapper.insert(book);
        return book;
    }

    @Transactional
    public Book update(BookSaveRequest request) {
        if (request.getId() == null) {
            throw new BusinessException("图书 ID 不能为空");
        }
        Book book = requireBook(request.getId());
        assertIsbnAvailable(request.getIsbn().trim(), request.getId());

        int oldTotalStock = book.getTotalStock();
        int oldAvailableStock = book.getAvailableStock();
        int borrowedCount = oldTotalStock - oldAvailableStock;
        if (request.getTotalStock() < borrowedCount) {
            throw new BusinessException("馆藏数量不能小于当前借出数量 " + borrowedCount);
        }

        applyRequest(book, request);
        book.setAvailableStock(oldAvailableStock + request.getTotalStock() - oldTotalStock);
        bookMapper.updateById(book);
        return book;
    }

    @Transactional
    public void delete(Long id) {
        Book book = requireBook(id);
        long activeBorrows = borrowRecordMapper.selectCount(Wrappers.<BorrowRecord>lambdaQuery()
                .eq(BorrowRecord::getBookId, id)
                .eq(BorrowRecord::getStatus, "BORROWED"));
        if (activeBorrows > 0) {
            throw new BusinessException("该图书仍有借出记录，暂不能删除");
        }
        bookMapper.deleteById(book.getId());
    }

    private Book requireBook(Long id) {
        Book book = bookMapper.selectById(id);
        if (book == null) {
            throw new BusinessException("图书不存在");
        }
        return book;
    }

    private void assertIsbnAvailable(String isbn, Long excludeId) {
        Long count = bookMapper.selectCount(Wrappers.<Book>lambdaQuery()
                .eq(Book::getIsbn, isbn)
                .ne(excludeId != null, Book::getId, excludeId));
        if (count > 0) {
            throw new BusinessException("该 ISBN 已存在");
        }
    }

    private void applyRequest(Book book, BookSaveRequest request) {
        book.setIsbn(request.getIsbn().trim());
        book.setTitle(request.getTitle().trim());
        book.setAuthor(request.getAuthor().trim());
        book.setPublisher(StringUtils.hasText(request.getPublisher()) ? request.getPublisher().trim() : null);
        book.setCategory(StringUtils.hasText(request.getCategory()) ? request.getCategory().trim() : "未分类");
        book.setPrice(request.getPrice());
        book.setTotalStock(request.getTotalStock());
        book.setDescription(StringUtils.hasText(request.getDescription()) ? request.getDescription().trim() : null);
    }
}

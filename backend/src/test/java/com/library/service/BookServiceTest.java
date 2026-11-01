package com.library.service;

import com.library.common.BusinessException;
import com.library.dto.BookSaveRequest;
import com.library.entity.Book;
import com.library.mapper.BookMapper;
import com.library.mapper.BorrowRecordMapper;
import com.library.support.MybatisPlusTestSupport;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BookServiceTest {

    @BeforeAll
    static void initTableInfo() {
        MybatisPlusTestSupport.initTableInfo(Book.class);
    }

    @Mock
    private BookMapper bookMapper;
    @Mock
    private BorrowRecordMapper borrowRecordMapper;

    @InjectMocks
    private BookService bookService;

    @Test
    @DisplayName("新增图书：ISBN 重复时拒绝")
    void create_whenIsbnExists_thenReject() {
        BookSaveRequest request = request("9787111213826", 5);
        when(bookMapper.selectCount(any())).thenReturn(1L);

        assertThatThrownBy(() -> bookService.create(request))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("该 ISBN 已存在");
    }

    @Test
    @DisplayName("修改图书：馆藏数量不能小于当前借出数量")
    void update_whenTotalStockBelowBorrowed_thenReject() {
        Book existing = book(5, 2);
        when(bookMapper.selectById(1L)).thenReturn(existing);
        when(bookMapper.selectCount(any())).thenReturn(0L);

        BookSaveRequest request = request("9787111213826", 2);
        request.setId(1L);

        assertThatThrownBy(() -> bookService.update(request))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("馆藏数量不能小于当前借出数量 3");
    }

    @Test
    @DisplayName("修改图书：调大馆藏时同步增加可借库存")
    void update_whenTotalStockIncreased_thenAvailableStockFollows() {
        Book existing = book(5, 2);
        when(bookMapper.selectById(1L)).thenReturn(existing);
        when(bookMapper.selectCount(any())).thenReturn(0L);

        BookSaveRequest request = request("9787111213826", 8);
        request.setId(1L);

        Book updated = bookService.update(request);

        // 已借出 3 本，馆藏从 5 调到 8，可借库存应从 2 变为 5
        org.assertj.core.api.Assertions.assertThat(updated.getAvailableStock()).isEqualTo(5);
        verify(bookMapper).updateById(existing);
    }

    @Test
    @DisplayName("删除图书：仍有在借记录时拒绝")
    void delete_whenActiveBorrowExists_thenReject() {
        when(bookMapper.selectById(1L)).thenReturn(book(5, 2));
        when(borrowRecordMapper.selectCount(any())).thenReturn(1L);

        assertThatThrownBy(() -> bookService.delete(1L))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("该图书仍有借出记录");
    }

    @Test
    @DisplayName("删除图书：无在借记录时执行逻辑删除")
    void delete_whenNoActiveBorrow_thenDelete() {
        when(bookMapper.selectById(1L)).thenReturn(book(5, 5));
        when(borrowRecordMapper.selectCount(any())).thenReturn(0L);

        bookService.delete(1L);

        verify(bookMapper).deleteById(1L);
    }

    private BookSaveRequest request(String isbn, int totalStock) {
        BookSaveRequest request = new BookSaveRequest();
        request.setIsbn(isbn);
        request.setTitle("Java编程思想");
        request.setAuthor("Bruce Eckel");
        request.setPublisher("机械工业出版社");
        request.setCategory("计算机");
        request.setPrice(new BigDecimal("108.00"));
        request.setTotalStock(totalStock);
        return request;
    }

    private Book book(int totalStock, int availableStock) {
        Book book = new Book();
        book.setId(1L);
        book.setTitle("Java编程思想");
        book.setIsbn("9787111213826");
        book.setTotalStock(totalStock);
        book.setAvailableStock(availableStock);
        return book;
    }
}

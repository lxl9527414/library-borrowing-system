package com.library.ai.vo;

import com.library.entity.Book;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;

/**
 * AI 工具返回的图书摘要（契约 §2.3）。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class AiBookVO {

    private Long id;
    private String isbn;
    private String title;
    private String author;
    private String publisher;
    private String category;
    private BigDecimal price;
    private Integer totalStock;
    private Integer availableStock;
    private String description;

    public static AiBookVO from(Book book) {
        if (book == null) {
            return null;
        }
        return new AiBookVO(
                book.getId(),
                book.getIsbn(),
                book.getTitle(),
                book.getAuthor(),
                book.getPublisher(),
                book.getCategory(),
                book.getPrice(),
                book.getTotalStock(),
                book.getAvailableStock(),
                book.getDescription());
    }
}

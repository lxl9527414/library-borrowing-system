package com.library.dto;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.math.BigDecimal;

@Data
public class BookSaveRequest {

    private Long id;

    @NotBlank(message = "请输入 ISBN")
    @Size(max = 32, message = "ISBN 不能超过 32 个字符")
    private String isbn;

    @NotBlank(message = "请输入书名")
    @Size(max = 150, message = "书名不能超过 150 个字符")
    private String title;

    @NotBlank(message = "请输入作者")
    @Size(max = 100, message = "作者不能超过 100 个字符")
    private String author;

    @Size(max = 100, message = "出版社不能超过 100 个字符")
    private String publisher;

    @Size(max = 50, message = "分类不能超过 50 个字符")
    private String category;

    @NotNull(message = "请输入价格")
    @DecimalMin(value = "0.00", message = "价格不能小于 0")
    private BigDecimal price;

    @NotNull(message = "请输入馆藏数量")
    @Min(value = 0, message = "馆藏数量不能小于 0")
    private Integer totalStock;

    @Size(max = 1000, message = "简介不能超过 1000 个字符")
    private String description;
}


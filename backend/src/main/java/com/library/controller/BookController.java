package com.library.controller;

import com.library.common.ApiResponse;
import com.library.common.PageResult;
import com.library.dto.BookSaveRequest;
import com.library.entity.Book;
import com.library.log.Auditable;
import com.library.security.RequireRole;
import com.library.service.BookService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/books")
@RequiredArgsConstructor
public class BookController {

    private final BookService bookService;

    @GetMapping
    public ApiResponse<PageResult<Book>> page(
            @RequestParam(defaultValue = "1") long current,
            @RequestParam(defaultValue = "10") long size,
            @RequestParam(required = false) String keyword,
            @RequestParam(required = false) String category) {
        return ApiResponse.ok(PageResult.of(bookService.page(current, size, keyword, category)));
    }

    @GetMapping("/categories")
    public ApiResponse<List<String>> categories() {
        return ApiResponse.ok(bookService.categories());
    }

    @RequireRole("ADMIN")
    @PostMapping
    @Auditable(module = "图书", action = "新增图书")
    public ApiResponse<Book> create(@Valid @RequestBody BookSaveRequest request) {
        return ApiResponse.ok(bookService.create(request));
    }

    @RequireRole("ADMIN")
    @PutMapping("/{id}")
    @Auditable(module = "图书", action = "修改图书")
    public ApiResponse<Book> update(@PathVariable Long id, @Valid @RequestBody BookSaveRequest request) {
        request.setId(id);
        return ApiResponse.ok(bookService.update(request));
    }

    @RequireRole("ADMIN")
    @DeleteMapping("/{id}")
    @Auditable(module = "图书", action = "删除图书")
    public ApiResponse<Void> delete(@PathVariable Long id) {
        bookService.delete(id);
        return ApiResponse.ok();
    }
}

package com.library.controller;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;

@Controller
public class SpaController {

    /**
     * SPA 前端路由白名单：新增页面时必须同步加到这里，否则打包成 jar 后直接访问该路径会 404
     * （Vite dev server 有 history fallback，所以本地开发时发现不了这个问题）。
     */
    @GetMapping({"/login", "/dashboard", "/chat", "/books", "/borrows", "/reservations",
            "/users", "/rules", "/logs"})
    public String forwardToIndex() {
        return "forward:/index.html";
    }
}

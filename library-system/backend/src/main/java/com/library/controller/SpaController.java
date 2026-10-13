package com.library.controller;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;

@Controller
public class SpaController {

    @GetMapping({"/login", "/dashboard", "/books", "/borrows", "/reservations", "/users", "/rules", "/logs"})
    public String forwardToIndex() {
        return "forward:/index.html";
    }
}

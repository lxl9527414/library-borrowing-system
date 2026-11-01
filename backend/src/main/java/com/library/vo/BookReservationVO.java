package com.library.vo;

import com.baomidou.mybatisplus.annotation.TableField;
import com.library.entity.BookReservation;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class BookReservationVO extends BookReservation {

    private String bookTitle;
    private String bookIsbn;
    private String bookCategory;
    private String username;
    private String realName;

    /** 排队序号：仅排队中的记录返回，1 表示队首。 */
    @TableField(exist = false)
    private Integer queuePosition;
}

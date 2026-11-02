package com.library.vo;

import com.baomidou.mybatisplus.annotation.TableField;
import com.library.entity.BorrowRecord;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class BorrowRecordVO extends BorrowRecord {

    private String username;
    private String realName;

    @TableField(exist = false)
    private Boolean overdue;
}


package com.library.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.library.entity.BorrowRecord;
import com.library.vo.BorrowRecordVO;
import org.apache.ibatis.annotations.Param;

public interface BorrowRecordMapper extends BaseMapper<BorrowRecord> {

    IPage<BorrowRecordVO> selectRecordPage(
            IPage<BorrowRecordVO> page,
            @Param("userId") Long userId,
            @Param("status") String status,
            @Param("keyword") String keyword
    );
}

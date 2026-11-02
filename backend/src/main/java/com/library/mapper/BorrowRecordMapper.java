package com.library.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.library.entity.BorrowRecord;
import com.library.vo.BorrowRecordVO;
import com.library.vo.DashboardStatVO;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface BorrowRecordMapper extends BaseMapper<BorrowRecord> {

    IPage<BorrowRecordVO> selectRecordPage(
            IPage<BorrowRecordVO> page,
            @Param("userId") Long userId,
            @Param("status") String status,
            @Param("keyword") String keyword
    );

    List<DashboardStatVO> selectTopBorrowedBooks(@Param("userId") Long userId);

    List<DashboardStatVO> selectBorrowCategoryStats(@Param("userId") Long userId);
}

package com.library.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.library.entity.BookReservation;
import com.library.vo.BookReservationVO;
import org.apache.ibatis.annotations.Param;

public interface BookReservationMapper extends BaseMapper<BookReservation> {

    IPage<BookReservationVO> selectReservationPage(
            IPage<BookReservationVO> page,
            @Param("userId") Long userId,
            @Param("status") String status,
            @Param("keyword") String keyword
    );
}

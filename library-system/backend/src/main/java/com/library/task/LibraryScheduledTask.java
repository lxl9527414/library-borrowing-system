package com.library.task;

import com.library.service.OperationLogService;
import com.library.service.ReservationService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class LibraryScheduledTask {

    private static final int LOG_RETENTION_DAYS = 90;

    private final ReservationService reservationService;
    private final OperationLogService operationLogService;

    /**
     * 每小时检查一次：待取书超过保留期的预约自动失效，并把图书让给下一位读者。
     */
    @Scheduled(cron = "0 0 * * * ?")
    public void releaseExpiredReservations() {
        int released = reservationService.expireReadyReservations();
        if (released > 0) {
            log.info("预约保留超时释放 {} 条，已顺延给下一位读者", released);
        }
    }

    /**
     * 每天凌晨 3 点清理过期操作日志。
     */
    @Scheduled(cron = "0 0 3 * * ?")
    public void cleanupOperationLogs() {
        int removed = operationLogService.cleanup(LOG_RETENTION_DAYS);
        if (removed > 0) {
            log.info("清理 {} 天前的操作日志 {} 条", LOG_RETENTION_DAYS, removed);
        }
    }
}

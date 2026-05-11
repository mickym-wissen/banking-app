package com.demo.banking.dto;

import com.demo.banking.model.Transaction;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import lombok.*;

import java.math.BigDecimal;
import java.time.LocalDateTime;

public class TransactionDto {

    /* ── DEPOSIT / WITHDRAWAL REQUEST ─────────────────────────────────── */
    @Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
    public static class MoneyRequest {
        @NotNull @Positive(message = "Amount must be positive")
        private BigDecimal amount;
        private String description;
    }

    /* ── TRANSFER REQUEST ──────────────────────────────────────────────── */
    @Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
    public static class TransferRequest {
        @NotNull private Long toAccountId;
        @NotNull @Positive private BigDecimal amount;
        private String description;
    }

    /* ── RESPONSE ──────────────────────────────────────────────────────── */
    @Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
    public static class Response {
        private Long id;
        private Long accountId;
        private Transaction.TransactionType type;
        private BigDecimal amount;
        private BigDecimal balanceAfter;
        private String description;
        private LocalDateTime createdAt;

        public static Response from(Transaction t) {
            return Response.builder()
                    .id(t.getId())
                    .accountId(t.getAccount().getId())
                    .type(t.getType())
                    .amount(t.getAmount())
                    .balanceAfter(t.getBalanceAfter())
                    .description(t.getDescription())
                    .createdAt(t.getCreatedAt())
                    .build();
        }
    }
}

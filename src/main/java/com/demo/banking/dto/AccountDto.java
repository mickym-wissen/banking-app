package com.demo.banking.dto;

import com.demo.banking.model.Account;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.PositiveOrZero;
import lombok.*;

import java.math.BigDecimal;
import java.time.LocalDateTime;

public class AccountDto {

    /* ── CREATE ────────────────────────────────────────────────────────── */
    @Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
    public static class CreateRequest {
        @NotBlank(message = "Owner name is required")
        private String ownerName;

        @NotNull(message = "Account type is required")
        private Account.AccountType accountType;

        @NotNull(message = "Initial balance is required")
        @PositiveOrZero(message = "Balance must be zero or positive")
        private BigDecimal initialBalance;
    }

    /* ── UPDATE ────────────────────────────────────────────────────────── */
    @Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
    public static class UpdateRequest {
        private String ownerName;
        private Account.AccountStatus status;
    }

    /* ── RESPONSE ──────────────────────────────────────────────────────── */
    @Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
    public static class Response {
        private Long id;
        private String ownerName;
        private String accountNumber;
        private Account.AccountType accountType;
        private BigDecimal balance;
        private Account.AccountStatus status;
        private LocalDateTime createdAt;
        private LocalDateTime updatedAt;

        public static Response from(Account a) {
            return Response.builder()
                    .id(a.getId())
                    .ownerName(a.getOwnerName())
                    .accountNumber(a.getAccountNumber())
                    .accountType(a.getAccountType())
                    .balance(a.getBalance())
                    .status(a.getStatus())
                    .createdAt(a.getCreatedAt())
                    .updatedAt(a.getUpdatedAt())
                    .build();
        }
    }
}

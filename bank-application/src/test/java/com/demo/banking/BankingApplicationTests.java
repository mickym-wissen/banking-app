package com.demo.banking;

import com.demo.banking.dto.AccountDto;
import com.demo.banking.dto.TransactionDto;
import com.demo.banking.model.Account;
import com.demo.banking.service.AccountService;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.math.BigDecimal;

import static org.hamcrest.Matchers.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest
@AutoConfigureMockMvc
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class BankingApplicationTests {

    @Autowired MockMvc mockMvc;
    @Autowired ObjectMapper objectMapper;
    @Autowired AccountService accountService;

    static Long account1Id;
    static Long account2Id;

    @Test @Order(1)
    void createAccount_success() throws Exception {
        var req = AccountDto.CreateRequest.builder()
                .ownerName("Alice Smith")
                .accountType(Account.AccountType.SAVINGS)
                .initialBalance(BigDecimal.valueOf(1000))
                .build();

        var result = mockMvc.perform(post("/api/v1/accounts")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.ownerName", is("Alice Smith")))
                .andExpect(jsonPath("$.balance", is(1000)))
                .andReturn();

        var resp = objectMapper.readValue(
                result.getResponse().getContentAsString(), AccountDto.Response.class);
        account1Id = resp.getId();
    }

    @Test @Order(2)
    void createSecondAccount() throws Exception {
        var req = AccountDto.CreateRequest.builder()
                .ownerName("Bob Jones")
                .accountType(Account.AccountType.CHECKING)
                .initialBalance(BigDecimal.valueOf(500))
                .build();

        var result = mockMvc.perform(post("/api/v1/accounts")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isCreated())
                .andReturn();

        var resp = objectMapper.readValue(
                result.getResponse().getContentAsString(), AccountDto.Response.class);
        account2Id = resp.getId();
    }

    @Test @Order(3)
    void getAllAccounts() throws Exception {
        mockMvc.perform(get("/api/v1/accounts"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$", hasSize(greaterThanOrEqualTo(2))));
    }

    @Test @Order(4)
    void getAccountById_success() throws Exception {
        mockMvc.perform(get("/api/v1/accounts/" + account1Id))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id", is(account1Id.intValue())));
    }

    @Test @Order(5)
    void getAccountById_notFound() throws Exception {
        mockMvc.perform(get("/api/v1/accounts/9999"))
                .andExpect(status().isNotFound());
    }

    @Test @Order(6)
    void updateAccount() throws Exception {
        var req = AccountDto.UpdateRequest.builder()
                .ownerName("Alice Johnson")
                .build();

        mockMvc.perform(put("/api/v1/accounts/" + account1Id)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.ownerName", is("Alice Johnson")));
    }

    @Test @Order(7)
    void deposit() throws Exception {
        var req = new TransactionDto.MoneyRequest(BigDecimal.valueOf(200), "Salary");

        mockMvc.perform(post("/api/v1/accounts/" + account1Id + "/deposit")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.amount", is(200)))
                .andExpect(jsonPath("$.balanceAfter", is(1200)));
    }

    @Test @Order(8)
    void withdraw_success() throws Exception {
        var req = new TransactionDto.MoneyRequest(BigDecimal.valueOf(100), "ATM");

        mockMvc.perform(post("/api/v1/accounts/" + account1Id + "/withdraw")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.balanceAfter", is(1100)));
    }

    @Test @Order(9)
    void withdraw_insufficientFunds() throws Exception {
        var req = new TransactionDto.MoneyRequest(BigDecimal.valueOf(99999), "Overdraft");

        mockMvc.perform(post("/api/v1/accounts/" + account1Id + "/withdraw")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isBadRequest());
    }

    @Test @Order(10)
    void transfer_success() throws Exception {
        var req = TransactionDto.TransferRequest.builder()
                .toAccountId(account2Id)
                .amount(BigDecimal.valueOf(300))
                .description("Rent payment")
                .build();

        mockMvc.perform(post("/api/v1/accounts/" + account1Id + "/transfer")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$", hasSize(2)));
    }

    @Test @Order(11)
    void getTransactionHistory() throws Exception {
        mockMvc.perform(get("/api/v1/accounts/" + account1Id + "/transactions"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$", not(empty())));
    }

    @Test @Order(12)
    void deleteAccount() throws Exception {
        mockMvc.perform(delete("/api/v1/accounts/" + account1Id))
                .andExpect(status().isNoContent());

        mockMvc.perform(get("/api/v1/accounts/" + account1Id))
                .andExpect(status().isNotFound());
    }
}

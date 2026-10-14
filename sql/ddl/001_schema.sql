-- ============================================================
-- 合同审批审查系统 建表 DDL
-- MySQL 8.0.16+ / InnoDB / utf8mb4_0900_ai_ci
-- 所有表均含 created_at / updated_at，见各表定义
-- ============================================================

SET NAMES utf8mb4;

-- ---------- 1. 用户 ----------
CREATE TABLE IF NOT EXISTS app_user (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    username      VARCHAR(64)     NOT NULL                COMMENT '登录名',
    display_name  VARCHAR(64)     NOT NULL                COMMENT '显示姓名',
    role_code     VARCHAR(16)     NOT NULL                COMMENT 'LEGAL/BIZ/ADMIN',
    company_name  VARCHAR(128)    NULL                    COMMENT '所属企业全称，用于 R-SUB-03',
    is_active     TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否启用',
    created_at    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_username (username),
    KEY idx_role (role_code),
    CONSTRAINT ck_user_role CHECK (role_code IN ('LEGAL','BIZ','ADMIN'))
) ENGINE=InnoDB COMMENT='用户（课程项目用预置账号，不做注册）';

-- ---------- 2. 审查任务 ----------
CREATE TABLE IF NOT EXISTS contract_task (
    id                     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_no                VARCHAR(32)     NOT NULL             COMMENT 'CR+yyyyMMdd+6位序号',
    contract_name          VARCHAR(255)    NOT NULL,
    contract_type          VARCHAR(16)     NOT NULL DEFAULT 'UNKNOWN' COMMENT 'PURCHASE/SALES/SERVICE/LABOR/UNKNOWN',
    contract_no            VARCHAR(64)     NULL                 COMMENT '合同编号（正文抽取）',
    applicant_id           BIGINT UNSIGNED NOT NULL,
    applicant_dept         VARCHAR(64)     NULL,
    amount                 DECIMAL(18,2)   NULL,
    currency               CHAR(3)         NULL,
    our_party_role         CHAR(1)         NULL                 COMMENT 'A/B，我方角色',
    source_type            VARCHAR(16)     NOT NULL DEFAULT 'UPLOAD' COMMENT 'UPLOAD/APPROVAL_EVENT/APPROVAL_TODO',
    source_event_id        VARCHAR(64)     NULL,
    approval_no            VARCHAR(64)     NULL,
    status                 VARCHAR(16)     NOT NULL DEFAULT 'pending',
    block_reason           VARCHAR(32)     NULL,
    block_detail           VARCHAR(512)    NULL,
    retry_count            TINYINT UNSIGNED NOT NULL DEFAULT 0   COMMENT '解析重试次数，上限3',
    overall_risk_level     VARCHAR(8)      NULL                 COMMENT 'HIGH/MEDIUM/LOW',
    review_conclusion      VARCHAR(16)     NULL                 COMMENT 'PASS/RECTIFY/REJECT',
    review_summary         VARCHAR(1024)   NULL,
    review_degraded        TINYINT(1)      NOT NULL DEFAULT 0,
    review_degraded_reason VARCHAR(255)    NULL,
    review_version         INT UNSIGNED    NOT NULL DEFAULT 1,
    writeback_status       VARCHAR(16)     NOT NULL DEFAULT 'not_written',
    writeback_retry_count  TINYINT UNSIGNED NOT NULL DEFAULT 0   COMMENT '回写重试，上限3',
    parse_started_at       DATETIME(3)     NULL,
    review_finished_at     DATETIME(3)     NULL,
    close_reason           VARCHAR(255)    NULL,
    is_deleted             TINYINT(1)      NOT NULL DEFAULT 0,
    created_at             DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at             DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_task_no (task_no),
    UNIQUE KEY uk_source_event (source_event_id),
    KEY idx_status_risk_created (status, overall_risk_level, created_at),
    KEY idx_applicant (applicant_id, created_at),
    KEY idx_approval_no (approval_no),
    KEY idx_writeback_status (writeback_status),
    KEY idx_blocked (status, block_reason),
    CONSTRAINT fk_task_applicant FOREIGN KEY (applicant_id) REFERENCES app_user (id),
    CONSTRAINT ck_task_status CHECK (status IN ('pending','parsing','reviewing','blocked','completed','closed')),
    CONSTRAINT ck_task_writeback CHECK (writeback_status IN ('not_written','writing','success','failed')),
    CONSTRAINT ck_task_risk CHECK (overall_risk_level IS NULL OR overall_risk_level IN ('HIGH','MEDIUM','LOW')),
    CONSTRAINT ck_task_party_role CHECK (our_party_role IS NULL OR our_party_role IN ('A','B'))
) ENGINE=InnoDB COMMENT='审查任务（状态机主体）';

-- ---------- 3. 合同文件 ----------
CREATE TABLE IF NOT EXISTS contract_file (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id       BIGINT UNSIGNED NOT NULL,
    original_name VARCHAR(255)    NOT NULL                COMMENT '原始文件名，仅展示用',
    storage_path  VARCHAR(512)    NOT NULL                COMMENT '服务端生成路径',
    file_size     BIGINT UNSIGNED NOT NULL,
    file_md5      CHAR(32)        NOT NULL,
    mime_type     VARCHAR(128)    NOT NULL,
    file_ext      VARCHAR(16)     NOT NULL                COMMENT '小写，无点',
    parse_channel VARCHAR(16)     NULL                    COMMENT 'DOCX/PDF_TEXT/PDF_OCR/IMAGE_OCR',
    is_encrypted  TINYINT(1)      NOT NULL DEFAULT 0,
    page_count    INT             NULL                    COMMENT 'DOCX 为 NULL',
    file_version  INT UNSIGNED    NOT NULL DEFAULT 1,
    created_at    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_task (task_id),
    KEY idx_md5 (file_md5),
    CONSTRAINT fk_file_task FOREIGN KEY (task_id) REFERENCES contract_task (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='合同文件（每次重新上传递增 file_version）';

-- ---------- 4. 解析结果 ----------
CREATE TABLE IF NOT EXISTS parse_result (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id             BIGINT UNSIGNED NOT NULL,
    file_id             BIGINT UNSIGNED NOT NULL,
    version             INT UNSIGNED    NOT NULL DEFAULT 1   COMMENT '与 task.review_version 对齐',
    full_text           LONGTEXT        NOT NULL             COMMENT '段落以 \n 拼接，锚点全局偏移的基准',
    paragraph_tree      JSON            NOT NULL             COMMENT '段落树快照，供文档接口渲染',
    page_index          JSON            NULL                 COMMENT '页区间 [{page_no,char_start,char_end,width,height,render_ref}]',
    split_strategy      VARCHAR(64)     NOT NULL,
    page_count          INT             NULL,
    char_count          INT             NOT NULL DEFAULT 0,
    paragraph_count     INT             NOT NULL DEFAULT 0,
    ocr_engine          VARCHAR(32)     NULL,
    ocr_avg_confidence  DECIMAL(4,3)    NULL,
    quality_warnings    JSON            NULL,
    parse_duration_ms   INT             NOT NULL DEFAULT 0,
    ir_status           VARCHAR(16)     NOT NULL DEFAULT 'success' COMMENT 'success/partial',
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_task_version (task_id, version),
    KEY idx_file (file_id),
    CONSTRAINT fk_pr_task FOREIGN KEY (task_id) REFERENCES contract_task (id) ON DELETE CASCADE,
    CONSTRAINT fk_pr_file FOREIGN KEY (file_id) REFERENCES contract_file (id)
) ENGINE=InnoDB COMMENT='解析结果（含 IR 快照，重试时按 version 覆盖）';

-- ---------- 5. 合同元数据 ----------
CREATE TABLE IF NOT EXISTS contract_metadata (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id          BIGINT UNSIGNED NOT NULL,
    field_key        VARCHAR(48)     NOT NULL,
    field_label      VARCHAR(64)     NOT NULL,
    field_value      VARCHAR(512)    NULL,
    field_value_raw  VARCHAR(1024)   NULL                    COMMENT '原文表述 / 多候选 JSON',
    value_type       VARCHAR(16)     NOT NULL DEFAULT 'string',
    is_required      TINYINT(1)      NOT NULL DEFAULT 0,
    is_missing       TINYINT(1)      NOT NULL DEFAULT 0,
    source           VARCHAR(16)     NOT NULL DEFAULT 'auto' COMMENT 'auto/manual',
    confidence       DECIMAL(4,3)    NULL,
    anchor           JSON            NULL                    COMMENT 'Anchor 结构；无锚点为 NULL',
    updated_by       BIGINT UNSIGNED NULL,
    created_at       DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at       DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_task_field (task_id, field_key),
    CONSTRAINT fk_meta_task FOREIGN KEY (task_id) REFERENCES contract_task (id) ON DELETE CASCADE,
    CONSTRAINT fk_meta_user FOREIGN KEY (updated_by) REFERENCES app_user (id)
) ENGINE=InnoDB COMMENT='合同元数据（按字段一行，便于单字段修正）';

-- ---------- 6. 条款 ----------
CREATE TABLE IF NOT EXISTS clause (
    id                       BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    task_id                  BIGINT UNSIGNED  NOT NULL,
    parse_result_id          BIGINT UNSIGNED  NOT NULL,
    parent_clause_id         BIGINT UNSIGNED  NULL             COMMENT '长条款拆分的父条款',
    part_no                  INT              NULL             COMMENT '子条款序号',
    clause_index             INT              NOT NULL         COMMENT '文档内顺序号，1 起',
    clause_no                VARCHAR(32)      NULL             COMMENT '编号前缀原文',
    clause_level             TINYINT UNSIGNED NOT NULL DEFAULT 0,
    clause_title             VARCHAR(255)     NULL,
    clause_text              TEXT             NOT NULL         COMMENT '去编号前缀的正文，供模型与检索',
    clause_type              VARCHAR(32)      NOT NULL DEFAULT 'OTHER',
    clause_type_secondary    VARCHAR(32)      NULL,
    clause_type_confidence   DECIMAL(4,3)     NULL,
    classify_rule_hit        VARCHAR(255)     NULL,
    split_strategy           VARCHAR(24)      NOT NULL,
    paragraph_index_start    INT              NOT NULL,
    paragraph_index_end      INT              NOT NULL,
    page_start               INT              NULL,
    page_end                 INT              NULL,
    global_char_start        INT              NOT NULL,
    global_char_end          INT              NOT NULL,
    anchor                   JSON             NOT NULL         COMMENT 'Anchor 结构（与显式列同源）',
    char_count               INT              NOT NULL DEFAULT 0,
    created_at               DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at               DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_task_index (task_id, clause_index),
    KEY idx_task_type (task_id, clause_type),
    KEY idx_parent (parent_clause_id),
    KEY idx_char (task_id, global_char_start),
    FULLTEXT KEY ft_clause_text (clause_text) WITH PARSER ngram,
    CONSTRAINT fk_clause_task FOREIGN KEY (task_id) REFERENCES contract_task (id) ON DELETE CASCADE,
    CONSTRAINT fk_clause_pr FOREIGN KEY (parse_result_id) REFERENCES parse_result (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='条款（anchor 与显式定位列同源写入，显式列供查询，anchor 供前端整包渲染）';

-- ---------- 7. 风险项 ----------
CREATE TABLE IF NOT EXISTS risk_item (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id           BIGINT UNSIGNED NOT NULL,
    clause_id         BIGINT UNSIGNED NULL                  COMMENT '缺项类风险为 NULL',
    rule_code         VARCHAR(16)     NULL                  COMMENT '命中规则；纯 LLM 风险为 NULL',
    risk_name         VARCHAR(128)    NOT NULL,
    risk_type         VARCHAR(24)     NOT NULL DEFAULT 'CLAUSE_RISK' COMMENT 'CLAUSE_RISK/MISSING_CLAUSE/METADATA_RISK',
    clause_type       VARCHAR(32)     NULL,
    risk_level        VARCHAR(8)      NOT NULL,
    source            VARCHAR(16)     NOT NULL              COMMENT 'rule/llm/rule+llm',
    risk_reason       TEXT            NOT NULL,
    legal_basis       TEXT            NULL,
    original_text     TEXT            NULL,
    anchor            JSON            NULL,
    anchor_precision  VARCHAR(16)     NULL                  COMMENT '冗余存储，供列表页直接展示降级标记',
    is_false_positive TINYINT(1)      NOT NULL DEFAULT 0,
    false_positive_by BIGINT UNSIGNED NULL,
    review_version    INT UNSIGNED    NOT NULL DEFAULT 1,
    created_at        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_dedup (task_id, review_version, rule_code, clause_id),
    KEY idx_task_level (task_id, review_version, risk_level),
    KEY idx_task_fp (task_id, is_false_positive),
    KEY idx_clause (clause_id),
    CONSTRAINT fk_risk_task FOREIGN KEY (task_id) REFERENCES contract_task (id) ON DELETE CASCADE,
    CONSTRAINT fk_risk_clause FOREIGN KEY (clause_id) REFERENCES clause (id) ON DELETE CASCADE,
    CONSTRAINT ck_risk_level CHECK (risk_level IN ('HIGH','MEDIUM','LOW')),
    CONSTRAINT ck_risk_source CHECK (source IN ('rule','llm','rule+llm'))
) ENGINE=InnoDB COMMENT='风险项；uk_dedup 保证同版本同规则同条款只记一条（缺项类 clause_id 为 NULL 时需注意）';

-- ---------- 8. 修改建议 ----------
CREATE TABLE IF NOT EXISTS suggestion (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    risk_item_id      BIGINT UNSIGNED NOT NULL,
    suggestion_source VARCHAR(16)     NOT NULL              COMMENT 'template/llm/manual',
    template_id       BIGINT UNSIGNED NULL,
    suggested_text    TEXT            NOT NULL,
    final_text        TEXT            NULL,
    status            VARCHAR(16)     NOT NULL DEFAULT 'pending' COMMENT 'pending/adopted/rejected',
    reject_reason     VARCHAR(255)    NULL,
    error_msg         VARCHAR(255)    NULL                  COMMENT '生成失败原因',
    confirmed_by      BIGINT UNSIGNED NULL,
    confirmed_at      DATETIME(3)     NULL,
    created_at        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_risk (risk_item_id),
    KEY idx_status (status),
    CONSTRAINT fk_sug_risk FOREIGN KEY (risk_item_id) REFERENCES risk_item (id) ON DELETE CASCADE,
    CONSTRAINT ck_sug_status CHECK (status IN ('pending','adopted','rejected'))
) ENGINE=InnoDB COMMENT='修改建议（一条风险最多一条建议）';

-- ---------- 9. 人工确认记录 ----------
CREATE TABLE IF NOT EXISTS human_confirm (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id      BIGINT UNSIGNED NOT NULL,
    risk_item_id BIGINT UNSIGNED NOT NULL,
    action       VARCHAR(24)     NOT NULL COMMENT 'adopted/adopted_modified/rejected/false_positive',
    text_before  TEXT            NULL,
    text_after   TEXT            NULL,
    operator_id  BIGINT UNSIGNED NOT NULL,
    review_version INT UNSIGNED  NOT NULL DEFAULT 1,
    created_at   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_task (task_id),
    KEY idx_risk (risk_item_id),
    CONSTRAINT fk_hc_risk FOREIGN KEY (risk_item_id) REFERENCES risk_item (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='人工确认留痕，采纳率指标 G-03 从此表统计';

-- ---------- 10. 法务批注 ----------
CREATE TABLE IF NOT EXISTS review_comment (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id    BIGINT UNSIGNED NOT NULL,
    content    VARCHAR(2000)   NOT NULL,
    author_id  BIGINT UNSIGNED NOT NULL,
    is_deleted TINYINT(1)      NOT NULL DEFAULT 0,
    created_at DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_task_alive (task_id, is_deleted, created_at),
    CONSTRAINT fk_rc_task FOREIGN KEY (task_id) REFERENCES contract_task (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='法务批注';

-- ---------- 11. 回写日志 ----------
CREATE TABLE IF NOT EXISTS writeback_log (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id         BIGINT UNSIGNED NOT NULL,
    writeback_no    VARCHAR(40)     NOT NULL,
    approval_no     VARCHAR(64)     NOT NULL,
    idempotency_key CHAR(64)        NOT NULL COMMENT 'sha256(task_id|review_version|content_sha)',
    content_sha256  CHAR(64)        NOT NULL,
    content_body    MEDIUMTEXT      NOT NULL COMMENT '规范化后的 Markdown 正文',
    request_body    MEDIUMTEXT      NOT NULL,
    response_body   MEDIUMTEXT      NULL,
    http_status     INT             NULL,
    result          VARCHAR(16)     NOT NULL COMMENT 'success/failed',
    error_msg       VARCHAR(512)    NULL,
    operator_id     BIGINT UNSIGNED NOT NULL,
    retry_no        TINYINT UNSIGNED NOT NULL DEFAULT 0,
    duration_ms     INT             NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_writeback_no (writeback_no),
    UNIQUE KEY uk_idem (task_id, idempotency_key),
    KEY idx_approval (approval_no),
    KEY idx_result (result, created_at),
    CONSTRAINT fk_wb_task FOREIGN KEY (task_id) REFERENCES contract_task (id) ON DELETE CASCADE,
    CONSTRAINT ck_wb_result CHECK (result IN ('success','failed'))
) ENGINE=InnoDB COMMENT='回写日志；uk_idem 是回写幂等的第二道防线（第一道在 Mock 端）';

-- ---------- 12. 重试日志 ----------
CREATE TABLE IF NOT EXISTS task_retry_log (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id      BIGINT UNSIGNED NOT NULL,
    retry_no     TINYINT UNSIGNED NOT NULL,
    trigger_mode VARCHAR(16)     NOT NULL COMMENT 'manual/auto',
    retry_target VARCHAR(24)     NOT NULL DEFAULT 'parse' COMMENT 'parse/ocr/llm/writeback',
    result       VARCHAR(16)     NOT NULL COMMENT 'success/failed',
    error_msg    VARCHAR(512)    NULL,
    operator_id  BIGINT UNSIGNED NULL COMMENT '自动重试为 NULL',
    created_at   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_task_target (task_id, retry_target, created_at),
    CONSTRAINT fk_trl_task FOREIGN KEY (task_id) REFERENCES contract_task (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='重试日志，含自动重试与人工重试';

-- ---------- 13. 审查规则 ----------
CREATE TABLE IF NOT EXISTS risk_rule (
    id                       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    rule_code                VARCHAR(16)     NOT NULL COMMENT '如 R-LIA-01',
    rule_name                VARCHAR(128)    NOT NULL,
    rule_group               VARCHAR(32)     NOT NULL,
    applies_to_contract_type VARCHAR(64)     NOT NULL DEFAULT 'ALL' COMMENT '逗号分隔多个类型',
    condition_ast            JSON            NOT NULL COMMENT '条件 AST（见第 11 章）',
    condition_text           TEXT            NOT NULL COMMENT 'AST 的人类可读表达式，供界面展示与排查',
    risk_level               VARCHAR(8)      NOT NULL,
    basis                    TEXT            NOT NULL COMMENT '判定依据，未确认时写「依据：待法务确认」',
    fix_direction            TEXT            NOT NULL,
    threshold_value          DECIMAL(10,4)   NULL,
    is_enabled               TINYINT(1)      NOT NULL DEFAULT 1,
    priority                 INT             NOT NULL DEFAULT 100,
    version                  INT UNSIGNED    NOT NULL DEFAULT 1,
    created_at               DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at               DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_rule_code (rule_code),
    KEY idx_enabled_priority (is_enabled, priority),
    KEY idx_group (rule_group),
    CONSTRAINT ck_rule_level CHECK (risk_level IN ('HIGH','MEDIUM','LOW'))
) ENGINE=InnoDB COMMENT='审查规则；由 seed/rules.yaml 导入，本期不做界面化编辑';

-- ---------- 14. 示范条款模板 ----------
CREATE TABLE IF NOT EXISTS clause_template (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    template_name VARCHAR(128)    NOT NULL,
    contract_type VARCHAR(16)     NOT NULL,
    clause_type   VARCHAR(32)     NOT NULL,
    rule_code     VARCHAR(16)     NULL COMMENT '关联规则，命中时优先于 clause_type 匹配',
    template_text TEXT            NOT NULL,
    is_enabled    TINYINT(1)      NOT NULL DEFAULT 1,
    remark        VARCHAR(255)    NULL,
    created_at    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_lookup (is_enabled, contract_type, clause_type),
    KEY idx_rule (rule_code)
) ENGINE=InnoDB COMMENT='示范条款模板库';

-- ---------- 15. 必检条款配置 ----------
CREATE TABLE IF NOT EXISTS required_clause_config (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    contract_type     VARCHAR(16)     NOT NULL,
    clause_type       VARCHAR(32)     NOT NULL,
    missing_rule_code VARCHAR(16)     NOT NULL COMMENT '缺失时触发的规则编号',
    is_enabled        TINYINT(1)      NOT NULL DEFAULT 1,
    created_at        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_type_clause (contract_type, clause_type),
    KEY idx_missing_rule (missing_rule_code)
) ENGINE=InnoDB COMMENT='必检条款清单，驱动缺项类风险（R-MIS-*）';

-- ---------- 16. Mock 审批单 ----------
CREATE TABLE IF NOT EXISTS mock_approval (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    approval_no    VARCHAR(64)     NOT NULL,
    contract_name  VARCHAR(255)    NOT NULL,
    applicant      VARCHAR(64)     NOT NULL,
    applicant_dept VARCHAR(64)     NULL,
    contract_type  VARCHAR(16)     NOT NULL,
    attachment_url VARCHAR(512)    NOT NULL COMMENT '指向本服务的 mock 附件下载地址',
    status         VARCHAR(16)     NOT NULL DEFAULT 'PENDING_REVIEW',
    pushed         TINYINT(1)      NOT NULL DEFAULT 0,
    created_at     DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at     DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_approval_no (approval_no),
    KEY idx_pushed (pushed, status)
) ENGINE=InnoDB COMMENT='Mock 审批系统的审批单';

-- ---------- 17. Mock 审批评论 ----------
CREATE TABLE IF NOT EXISTS mock_comment (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    approval_no VARCHAR(64)     NOT NULL,
    author      VARCHAR(64)     NOT NULL,
    content     MEDIUMTEXT      NOT NULL,
    source      VARCHAR(24)     NOT NULL DEFAULT 'REVIEW_SYSTEM',
    comment_no  INT             NOT NULL COMMENT '同一审批单内的评论序号',
    created_at  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_approval (approval_no, comment_no),
    CONSTRAINT fk_mc_approval FOREIGN KEY (approval_no) REFERENCES mock_approval (approval_no) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='Mock 审批系统的评论';

-- ---------- 18. Mock 幂等记录（ADR-006 的核心表） ----------
CREATE TABLE IF NOT EXISTS mock_idempotency (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    approval_no     VARCHAR(64)     NOT NULL,
    idempotency_key CHAR(64)        NOT NULL,
    comment_id      BIGINT UNSIGNED NOT NULL COMMENT '首次成功写入产生的评论 ID',
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_approval_idem (approval_no, idempotency_key),
    CONSTRAINT fk_mi_approval FOREIGN KEY (approval_no) REFERENCES mock_approval (approval_no) ON DELETE CASCADE,
    CONSTRAINT fk_mi_comment FOREIGN KEY (comment_id) REFERENCES mock_comment (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='Mock 端幂等表：同一幂等键只产生一条评论，重复请求返回既有 comment_id';

-- ---------- 19. LangGraph 检查点 ----------
CREATE TABLE IF NOT EXISTS lg_checkpoint (
    thread_id            VARCHAR(64)  NOT NULL COMMENT '用 task_id 作为 thread_id',
    checkpoint_ns        VARCHAR(64)  NOT NULL DEFAULT '',
    checkpoint_id        VARCHAR(64)  NOT NULL,
    parent_checkpoint_id VARCHAR(64)  NULL,
    type                 VARCHAR(32)  NOT NULL DEFAULT 'json',
    checkpoint           LONGBLOB     NOT NULL,
    metadata             JSON         NOT NULL,
    created_at           DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at           DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id),
    KEY idx_thread (thread_id, created_at)
) ENGINE=InnoDB COMMENT='LangGraph 自定义 MySQL 检查点（MySQL 无官方 Saver，见 19.1 R-04）';

CREATE TABLE IF NOT EXISTS lg_checkpoint_writes (
    thread_id     VARCHAR(64)  NOT NULL,
    checkpoint_ns VARCHAR(64)  NOT NULL DEFAULT '',
    checkpoint_id VARCHAR(64)  NOT NULL,
    task_id       VARCHAR(64)  NOT NULL COMMENT 'LangGraph 内部 task_id，非业务任务',
    idx           INT          NOT NULL,
    channel       VARCHAR(64)  NOT NULL,
    type          VARCHAR(32)  NULL,
    value         LONGBLOB     NULL,
    created_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx),
    KEY idx_thread (thread_id)
) ENGINE=InnoDB COMMENT='LangGraph 检查点中间写入';

-- ---------- 20. 审计日志 ----------
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    operator_id BIGINT UNSIGNED NULL,
    action      VARCHAR(32)     NOT NULL COMMENT 'CREATE_TASK/ADOPT_SUGGESTION/WRITEBACK/RETRY/UPDATE_RULE...',
    target_type VARCHAR(24)     NOT NULL COMMENT 'task/risk_item/rule/config/metadata',
    target_id   VARCHAR(64)     NULL,
    detail      JSON            NULL COMMENT '变更前后快照',
    ip          VARCHAR(45)     NULL,
    created_at  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_operator_time (operator_id, created_at),
    KEY idx_action_time (action, created_at),
    KEY idx_target (target_type, target_id)
) ENGINE=InnoDB COMMENT='审计日志：只追加，不提供修改删除接口';

INSERT IGNORE INTO knowledge_spaces
    (name, code, description, allowed_roles, is_enabled)
VALUES
    ('产品知识库', 'product', '产品功能、使用方法、常见问题', 'employee,service,admin', TRUE),
    ('企业制度库', 'policy', '企业制度、流程规范、培训资料', 'employee,service,admin', TRUE),
    ('售后知识库', 'service', '故障排查、维修SOP、保修政策', 'service,admin', TRUE),
    ('管理员空间', 'admin', '内部管理文档', 'admin', TRUE);

INSERT IGNORE INTO schema_migrations (version) VALUES ('002_knowledge_spaces');

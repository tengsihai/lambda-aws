"""Lambda 增删改查业务代码。

包布局约定：
    common/  跨函数共享的基础设施（配置、密钥、数据库、HTTP 封装）
    create/  新增资源
    read/    查询资源（单条 + 分页列表）
    update/  更新资源
    delete/  删除资源
"""

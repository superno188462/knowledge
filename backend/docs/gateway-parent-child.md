# Gateway 父子记录与版本发布

## 当前实现

Knowledge 使用独立 SDK agent-gateway-client 0.2.0，Gateway 服务端无需本项目修改。
勾选父子层级时，本地切片器先生成父块，再切子片。父块以 record_kind=document 写入 Gateway，正文在 content 中，不放 metadata；子片以 vector 写入，顶层 parent_id 引用父块外部 ID。
未勾选层级的文件只写 vector，同样使用版本发布流程。

记录共用 namespace（knowledge-platform:<已验证用户ID>）、document_id、version_id 和 staged=true。
先提交父块（每批最多64条），再提交子片；全部确认后调用 publish，随后提交本地活动版本，再清理旧子片和旧父块。
SDK按64条及4MiB请求大小双重约束分批，普通父正文最多20000字符，子片满足collection.chunk_size和6000字符硬上限。

## 版本、重试与失败

knowledge_documents.embedding_plan 保存请求签名、版本 ID、发布状态。正文/规则/metadata/目标改变生成新 UUID；同一计划重试使用相同 ID。正文回退也会生成新版本，不尝试重新发布退役版本。

- 写入失败：保留旧活动代及新暂存代映射，重试同一计划。不自动删除新暂存代。
- 开始发布前持久化 publishing 状态。发布超时或本地提交失败，显示 VECTOR_PUBLICATION_PENDING；Gateway可能已生效，不删除新记录。重试同一计划的写入/publish可确认结果。
- 发布结果不明时，禁止修改原文、删除该文档或切换Key/collection，先重试确认。
- 发布成功、清理失败：本地活动代仍为新版本，显示 cleanup_pending；映射保留，重试可继续清理。
- 先删旧子片，再删旧父块；父块仍被引用时Gateway拒绝删除。

## 检索

Gateway query返回新协议已发布子片，Knowledge读取顶层parent_id，去重后fetch父块，再返回content（子片）、context_content（父正文）、context_status。
父块缺失或fetch失败不丢弃子片，context_status=missing。
新协议记录不再依赖本地活动切片白名单判定Gateway发布可见性。旧协议记录保留原过滤兼容逻辑；旧父正文不会自动上传，需重新嵌入升级。
其他项目可直接使用SDK的similarity_search_with_context，也可自行query→fetch。SDK不会验证终端用户，调用方须自行认证、注入metadata过滤并按parent_id去重。

## 升级

本地（两个仓库相邻）：

```powershell
cd D:\github\课程\knowledge\backend
uv sync
uv run alembic upgrade head
uv run python main.py
```

数据库增量版本为20261010_07，只新增embedding_plan列，不删除旧文档。已有父子文档点击“重试/嵌入”即可重新生成父块及子片，旧数据在新发布确认后清理。
SDK模型前缀不变。无需增加父子专用环境变量。
服务器需要更新两个仓库、重建Docker镜像；Compose迁移容器会升级数据库。当前修改没有自动提交或推送。

## 边界

首次迁移旧协议记录时，它们没有版本范围，publish不能直接隐藏这些旧向量，需在发布后通过已记录的旧ID清理；短暂过渡或清理失败时直接Gateway调用方可能看到旧记录。
无父子模式的新协议记录也支持原子发布。query与后续fetch是两个请求；期间恰好发布新版本时父块可能已隐藏，返回missing并保留子片，可重新检索。
切换网关/集合只操作当前目标记录，不用新Key清理旧网关。现有目录仍按用户管理，集合目录独立隔离不在本次改动范围。
发布完整性由Knowledge检查所有写入响应；Gateway不会判断预期总记录数。后台仍为持久化队列、内置异步worker和线程池切片。

本次未执行自动测试或真实Gateway写入，未消耗Embedding额度；构建、语法检查和数据库迁移结果在交付说明中分别报告。

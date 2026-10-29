# 旧 STT 分组在订阅刷新后残留节点引用

触发：旧 capabilities 没有 `selections.STT`，订阅节点改名或下架后，候选生成器复制旧组，overlay 只重建 STT Download，导致旧 STT 引用失效。

修复：仅当清单缺少该选择项且 STT 组已存在时，按当前授权 STT 节点重建成员。保留位置、策略和元数据；显式选择项优先。原生计量模式须先用 `owned_group_members` 确认生成式归属，再移除旧 `use/filter`，避免 mixed ownership。不得无条件删除用户自定义 provider 字段。没有可用 STT 节点时仍拒绝候选。

验证：`python3 tools/check_legacy_stt.py` 覆盖暂停/原生计量两种模式的改名、删除、显式选择、无旧组、无节点、其它选择项和用户 provider；重复刷新须字节一致。设置 `MIHOMO_TEST_BINARY` 后，还会用实际核心 `-t` 校验合成候选的完整组与 provider 图。该检查不等同于实机激活验收。

# Scripts Package

## 文件说明

### `seed_production_data.py`
生产环境数据初始化脚本，用于首次部署或数据恢复时初始化默认审批规则。

**使用方法:**
```bash
# 初始化数据（如不存在）
.venv/bin/python scripts/seed_production_data.py

# 强制重置所有数据（慎用）
.venv/bin/python scripts/seed_production_data.py --force
```

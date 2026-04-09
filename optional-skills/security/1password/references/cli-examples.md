# op CLI 示例

## 登录和身份

```bash
op signin
op signin --account my.1password.com
op whoami
op account list
```

## 读取秘密

```bash
op read "op://app-prod/db/password"
op read "op://app-prod/npm/one-time password?attribute=otp"
```

## 注入秘密

```bash
echo "api_key: {{ op://app-prod/openai/api key }}" | op inject
op inject -i config.tpl.yml -o config.yml
```

## 使用秘密运行命令

```bash
export DB_PASSWORD="op://app-prod/db/password"
op run -- sh -c '[ -n "$DB_PASSWORD" ] && echo "DB_PASSWORD is set"'
```

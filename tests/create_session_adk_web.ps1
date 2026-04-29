$base = "http://localhost:8080"
$app = "agent"
$user = "user"
$session = "debug"

# 1. Создать сессию, если её ещё нет
Invoke-RestMethod `
  -Method Post `
  -Uri "$base/apps/$app/users/$user/sessions/$session" `
  -ContentType "application/json" `
  -Body "{}"

# 2. Записать контекст в session.state
Invoke-RestMethod `
  -Method Patch `
  -Uri "$base/apps/$app/users/$user/sessions/$session" `
  -ContentType "application/json" `
  -Body '{
    "stateDelta": {
      "first_name": "Vitaly",
      "last_name": "",
      "full_name": "Vitaly",
      "username": "debug",
      "region": "",
      "manager_group": false,
      "coach_group": false
    }
  }'

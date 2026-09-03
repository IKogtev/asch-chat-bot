<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Авторизация</title>
    <link rel="stylesheet" href="${url.resourcesPath}/css/custom.css">
    <link rel="apple-touch-icon" sizes="180x180" href="${url.resourcesPath}/img/apple-touch-icon.png">
    <link rel="icon" type="image/png" sizes="32x32" href="${url.resourcesPath}/img/favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href=""${url.resourcesPath}/img/favicon-16x16.png">
</head>
<body>
    <div id="login-screen" class="login-screen">
        <div class="login-box">
            <h2>🔐 Авторизация</h2>
            
            <form id="kc-form-login" action="${url.loginAction}" method="post">
                <input 
                    type="text" 
                    id="login-username" 
                    name="username" 
                    value="${(login.username!'')}" 
                    placeholder="Имя пользователя" 
                    autofocus 
                    autocomplete="off"
                    required
                >
                
                <input 
                    type="password" 
                    id="login-password" 
                    name="password" 
                    placeholder="Пароль" 
                    autocomplete="off"
                    required
                >

                <button type="submit" id="kc-login">Войти</button>
            </form>

            <div id="login-error">
                <#if message?? && (message.type == 'error' || message.type == 'warning')>
                    ${kcSanitize(message.summary)?no_esc}
                </#if>
            </div>
        </div>
    </div>
</body>
</html>
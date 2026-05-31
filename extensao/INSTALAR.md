# Como instalar a extensão TechDrop no Chrome

## Passo a passo (2 minutos)

1. Abra o Chrome e acesse: `chrome://extensions`
2. Ative o **Modo do desenvolvedor** (toggle no canto superior direito)
3. Clique em **Carregar sem compactação**
4. Selecione a pasta `extensao` dentro do projeto TechDrop
5. A extensão aparece na barra do Chrome com o ícone azul "T"

## Como usar

1. Abra qualquer produto no AliExpress
2. Clique no ícone **⚡ TechDrop** na barra do Chrome
3. Clique em **Capturar produto**
4. Confira as fotos, variantes e o preço calculado
5. Informe a URL do painel e seu token admin
6. Clique **Importar para o painel**

## Onde achar o token admin

No painel admin, abra o console do navegador (F12) e digite:
```
localStorage.getItem("admin_token")
```

Cole o resultado no campo Token da extensão.

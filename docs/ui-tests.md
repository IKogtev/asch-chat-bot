# установка чтобы была возможность запускать тесты
npm init -y
npm install -D playwright
npx playwright install
npm install -D typescript ts-node @types/node
npm install -D @playwright/test
npx playwright test --list

# запуск тестов: 
npx playwright test tests/ui/collections.spec.ts
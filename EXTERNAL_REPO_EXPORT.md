# Export current state to external repository

## 1) Bundle file
A ready-to-import bundle was created in repo root:
- BMI30_rpi_state_2026-01-31.bundle

## 2) Import on RPi
```bash
git clone BMI30_rpi_state_2026-01-31.bundle BMI30.stm32h7
cd BMI30.stm32h7
```

## 3) Attach external remote (optional)
```bash
git remote add origin <REMOTE_URL>
git push -u origin HEAD
```

## 4) Environment / build
Follow RPI_ENVIRONMENT.md

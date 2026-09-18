# Security Constraints
- **Secrets & Credentials**: Never read, view, grep, or output contents of `.env`, `.env.*`, shell configuration files (`~/.zshrc`, `~/.bashrc`, `~/.zshenv`, `~/.zprofile`, `~/.bash_profile`), or private credential files. They contain sensitive private credentials.

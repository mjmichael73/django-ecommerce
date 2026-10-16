# Troubleshooting — django-ecommerce

## Common Issues

### 1. Installation Issues

#### Issue: Dependencies not installing
```bash
# Solution: Clear cache and reinstall
npm cache clean --force
rm -rf node_modules
npm install
```

#### Issue: Python version mismatch
```bash
# Solution: Use correct Python version
python --version
pyenv install 3.11
pyenv local 3.11
```

### 2. Development Issues

#### Issue: Port already in use
```bash
# Solution: Find and kill process using the port
lsof -i :8080
kill -9 <PID>
```

#### Issue: Database connection refused
```bash
# Solution: Ensure database is running
docker compose up -d postgres
docker compose ps
```

### 3. Build Issues

#### Issue: Build fails with memory error
```bash
# Solution: Increase Node.js memory limit
NODE_OPTIONS="--max-old-space-size=4096" npm run build
```

#### Issue: TypeScript compilation errors
```bash
# Solution: Check TypeScript configuration
npx tsc --noEmit
# Fix errors in tsconfig.json or source files
```

### 4. Runtime Issues

#### Issue: Application crashes on startup
```bash
# Solution: Check environment variables
cat .env
# Ensure all required variables are set
```

#### Issue: High memory usage
```bash
# Solution: Check for memory leaks
node --inspect server.js
# Use Chrome DevTools to profile memory
```

### 5. Docker Issues

#### Issue: Container won't start
```bash
# Solution: Check container logs
docker compose logs <service-name>
# Check if ports are available
docker ps
```

#### Issue: Volume mount issues
```bash
# Solution: Check volume permissions
ls -la ./data
# Fix permissions if needed
chmod -R 755 ./data
```

## Getting Help

If you're still experiencing issues:

1. Check the [FAQ](#faq) section below
2. Search existing [GitHub Issues](../../issues)
3. Create a new issue with detailed information

## FAQ

### Q: How do I reset the database?
```bash
docker compose down -v
docker compose up -d
```

### Q: How do I update dependencies?
```bash
# For npm
npm update
# For pip
pip install --upgrade -r requirements.txt
```

### Q: How do I run tests?
```bash
# See README.md for project-specific test commands
```

## Environment-Specific Issues

### macOS
- Install Xcode Command Line Tools: `xcode-select --install`
- Use Homebrew for dependencies: `brew install <package>`

### Linux
- Install build essentials: `sudo apt-get install build-essential`
- Install Docker: Follow [official guide](https://docs.docker.com/engine/install/)

### Windows
- Use WSL2 for development
- Install Docker Desktop for Windows
- Use PowerShell or Windows Terminal

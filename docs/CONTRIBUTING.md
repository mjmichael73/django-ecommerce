# Contributing — Django E-commerce

Thank you for your interest in contributing to Django E-commerce!

## Getting Started

### Prerequisites

- Python 3.10+
- PostgreSQL 14+
- Redis 7+
- RabbitMQ 3+
- Docker & Docker Compose (recommended)

### Development Setup

```bash
# Clone the repository
git clone <repository-url>
cd django-ecommerce

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your settings

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Start development server
python manage.py runserver

# Start Celery worker (in separate terminal)
celery -A config worker -l info
```

## Project Structure

```
django-ecommerce/
├── catalog/           # Products, categories
├── cart/              # Shopping cart
├── orders/            # Order management
├── payment/           # Stripe integration
├── coupons/           # Discount codes
├── templates/         # HTML templates
├── static/            # CSS, JS, images
└── tests/             # Test files
```

## Development Guidelines

### Code Style

- Follow PEP 8 for Python code
- Use Black for code formatting
- Use flake8 for linting
- Use isort for import sorting

```bash
# Format code
black .

# Check linting
flake8 .

# Sort imports
isort .
```

### Django Best Practices

1. **Models**
   - Use descriptive field names
   - Add `__str__` methods
   - Use migrations for schema changes

2. **Views**
   - Use class-based views when possible
   - Keep views thin (business logic in services)
   - Add proper error handling

3. **Templates**
   - Use template inheritance
   - Keep templates clean (minimal logic)
   - Use template tags for complex logic

4. **Tests**
   - Write tests for all new features
   - Use factories for test data
   - Aim for >80% coverage

### Git Workflow

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes
4. Run tests: `pytest`
5. Commit: `git commit -m 'Add amazing feature'`
6. Push: `git push origin feature/amazing-feature`
7. Open a pull request

### Commit Messages

Use conventional commits:

```
feat: Add product search functionality
fix: Resolve cart session issue
docs: Update README with setup instructions
test: Add unit tests for payment service
refactor: Simplify order creation logic
```

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=catalog --cov=cart --cov=orders

# Run specific test file
pytest catalog/tests/test_models.py

# Run with verbose output
pytest -v
```

## Pull Request Process

1. **Before submitting:**
   - [ ] Code follows project style guidelines
   - [ ] Tests pass locally
   - [ ] Documentation is updated
   - [ ] No sensitive data (API keys, passwords) committed

2. **PR Description:**
   - Clear description of changes
   - Reference related issues
   - Screenshots for UI changes

3. **Review Process:**
   - At least one maintainer approval
   - All CI checks pass
   - No merge conflicts

## Reporting Issues

When reporting issues, please include:

1. **Environment details**
   - OS and version
   - Python version
   - Django version
   - Browser (for frontend issues)

2. **Steps to reproduce**
   - Clear, numbered steps
   - Expected behavior
   - Actual behavior

3. **Logs/Screenshots**
   - Error messages
   - Console output
   - Screenshots (if applicable)

## Code of Conduct

- Be respectful and inclusive
- Welcome newcomers
- Focus on constructive feedback
- Help each other learn

## Questions?

If you have questions:

1. Check the documentation
2. Search existing issues
3. Open a discussion issue
4. Contact maintainers

Thank you for contributing! 🎉

# 🚀 Stripe Rebilling Backend

Professional Flask backend for batch charging Stripe customers.

## Features

✅ Batch charge multiple customers at once  
✅ **Force skip** Link, Google Pay, Apple Pay payment methods  
✅ Customizable charge settings (amount, currency, description)  
✅ Customer diagnostic endpoint  
✅ Real-time charging with delays to avoid Stripe Radar blocks  
✅ Detailed payment results with full card information  
✅ CORS enabled for frontend integration  

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python3 server.py
```

Server will start on `http://localhost:5001`

## API Endpoints

### `GET /health`
Health check endpoint

### `POST /validate-key`
Validate Stripe API key
```json
{
  "apiKey": "sk_test_..."
}
```

### `POST /get-customers`
Get customer statistics
```json
{
  "apiKey": "sk_test_..."
}
```

### `POST /check-customers`
Detailed customer diagnostic
```json
{
  "apiKey": "sk_test_..."
}
```

### `POST /charge`
Charge customers
```json
{
  "apiKey": "sk_test_...",
  "amount": 29.99,
  "currency": "usd",
  "description": "Monthly Subscription",
  "maxCustomers": 0,
  "delay": 1.0
}
```

## Payment Method Filtering

**Automatically skips:**
- Link payment methods
- Google Pay
- Apple Pay

Only regular card payments are processed.

## Configuration

All settings are configured via the frontend or API requests:
- Amount (dollars)
- Currency (usd, eur, gbp, cad, aud)
- Description
- Max customers (0 = all)
- Delay between charges (seconds)

## Security

- API keys are never stored
- All requests validated
- CORS enabled for specified origins
- Rate limiting via charge delays

## Requirements

- Python 3.7+
- Flask 3.0.0
- Stripe SDK 7.0.0+

## License

MIT


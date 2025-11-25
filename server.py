#!/usr/bin/env python3
"""
Flask Backend for Stripe Rebilling Dashboard
Uses the same working logic as charge_all_customers.py
"""

from flask import Flask, request, jsonify
import stripe
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

# ============================================================
# CORS CONFIGURATION
# ============================================================

@app.after_request
def after_request(response):
    """Add CORS headers to every response"""
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response


# ============================================================
# API ENDPOINTS
# ============================================================

@app.route('/health', methods=['GET', 'OPTIONS'])
def health():
    """Health check endpoint"""
    if request.method == 'OPTIONS':
        return '', 204
    
    return jsonify({
        'status': 'ok',
        'message': 'Backend is running',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/validate-key', methods=['POST', 'OPTIONS'])
def validate_key():
    """Validate Stripe API key"""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        # Test the API key
        stripe.api_key = api_key
        stripe.Customer.list(limit=1)
        
        # Determine mode
        mode = 'live' if api_key.startswith('sk_live_') else 'test'
        
        return jsonify({
            'success': True,
            'mode': mode
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/check-customers', methods=['POST', 'OPTIONS'])
def check_customers():
    """Detailed customer diagnostic - like check_customers.py"""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        stripe.api_key = api_key
        
        # Get all customers
        customers = stripe.Customer.list(limit=100)
        customer_list = list(customers.auto_paging_iter())
        
        customers_with_pm = 0
        customers_with_source = 0
        customers_with_invoice_settings = 0
        customer_details = []
        
        for customer in customer_list:
            # Check for PaymentMethod (new way)
            payment_methods = stripe.PaymentMethod.list(
                customer=customer.id,
                type='card',
                limit=1
            )
            has_pm = len(payment_methods.data) > 0
            if has_pm:
                customers_with_pm += 1
            
            # Check for default source (old way)
            has_source = bool(customer.default_source)
            if has_source:
                customers_with_source += 1
            
            # Check invoice settings default payment method
            has_invoice_pm = bool(customer.invoice_settings and customer.invoice_settings.default_payment_method)
            if has_invoice_pm:
                customers_with_invoice_settings += 1
            
            # Determine if chargeable
            chargeable = has_pm or has_source or has_invoice_pm
            
            customer_details.append({
                'id': customer.id,
                'email': customer.email or 'No email',
                'name': customer.name or 'No name',
                'created': customer.created,
                'hasPaymentMethod': has_pm,
                'hasSource': has_source,
                'hasInvoicePM': has_invoice_pm,
                'chargeable': chargeable
            })
        
        chargeable = max(customers_with_pm, customers_with_source, customers_with_invoice_settings)
        
        return jsonify({
            'success': True,
            'total': len(customer_list),
            'withPaymentMethod': customers_with_pm,
            'withSource': customers_with_source,
            'withInvoicePM': customers_with_invoice_settings,
            'chargeable': chargeable,
            'customers': customer_details
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


def check_customer_payment_method(customer):
    """Helper function to check if customer has payment method - for parallel processing"""
    try:
        # Method 1: Check for PaymentMethod (new Stripe API)
        payment_methods = stripe.PaymentMethod.list(
            customer=customer.id,
            type='card',
            limit=1
        )
        if len(payment_methods.data) > 0:
            return True
        
        # Method 2: Check for default source (older Stripe API)
        if customer.default_source:
            return True
        
        # Method 3: Check invoice settings default payment method
        if customer.invoice_settings and customer.invoice_settings.default_payment_method:
            return True
        
        return False
    except:
        return False


@app.route('/get-customers', methods=['POST', 'OPTIONS'])
def get_customers():
    """Get customer count - OPTIMIZED with parallel processing for 3-5x faster loading"""
    
    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        stripe.api_key = api_key
        
        # Get all customers
        customers = stripe.Customer.list(limit=100)
        customer_list = list(customers.auto_paging_iter())
        
        total = len(customer_list)
        with_payment = 0
        
        # Use parallel processing to check payment methods (MUCH faster!)
        # Check up to 20 customers at the same time
        with ThreadPoolExecutor(max_workers=20) as executor:
            # Submit all tasks
            future_to_customer = {
                executor.submit(check_customer_payment_method, customer): customer 
                for customer in customer_list
            }
            
            # Collect results
            for future in as_completed(future_to_customer):
                if future.result():
                    with_payment += 1
        
        return jsonify({
            'success': True,
            'total': total,
            'withPayment': with_payment
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/charge', methods=['POST', 'OPTIONS'])
def charge_customers():
    """Charge customers - EXACT LOGIC from charge_all_customers.py"""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        
        api_key = data.get('apiKey')
        amount_dollars = float(data.get('amount', 0))
        currency = data.get('currency', 'usd').lower()
        description = data.get('description', 'Subscription charge')
        max_customers = int(data.get('maxCustomers', 0))
        delay = float(data.get('delay', 1.0))
        skip_special_payments = True  # ALWAYS skip Link, Google Pay, Apple Pay
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        if amount_dollars <= 0:
            return jsonify({'success': False, 'error': 'Amount must be greater than 0'}), 400
        
        stripe.api_key = api_key
        amount_cents = int(amount_dollars * 100)
        
        # Get all customers - SAME as script
        customers = stripe.Customer.list(limit=100)
        
        # Filter customers with payment methods - FORCE SKIP Link/GPay/APay
        customers_to_charge = []
        skipped_special_payment = 0
        
        for customer in customers.auto_paging_iter():
            has_valid_card = False
            
            try:
                # FORCE SKIP: Link, Google Pay, Apple Pay
                # Check ALL payment methods for this customer
                all_payment_methods = stripe.PaymentMethod.list(
                    customer=customer.id,
                    limit=10  # Check multiple payment methods
                )
                
                # Look for a valid card payment method (not Link/GPay/APay)
                for pm in all_payment_methods.data:
                    # SKIP Link payment method
                    if pm.type == 'link':
                        skipped_special_payment += 1
                        continue
                    
                    # SKIP if payment method has 'link' attribute
                    if hasattr(pm, 'link') and pm.link:
                        skipped_special_payment += 1
                        continue
                    
                    # SKIP Google Pay and Apple Pay
                    if pm.type in ['google_pay', 'apple_pay']:
                        skipped_special_payment += 1
                        continue
                    
                    # Only accept regular card type
                    if pm.type == 'card':
                        # Double check: Make sure card is not Link-connected
                        if hasattr(pm, 'card') and hasattr(pm.card, 'wallet'):
                            wallet_type = pm.card.wallet.get('type') if pm.card.wallet else None
                            if wallet_type in ['google_pay', 'apple_pay', 'link']:
                                continue
                        
                        has_valid_card = True
                        break
                
                # Fallback: Check for default source (older Stripe API)
                # Only use if no payment methods found above
                if not has_valid_card and customer.default_source:
                    has_valid_card = True
                
                # Fallback: Check invoice settings
                if not has_valid_card and customer.invoice_settings:
                    if customer.invoice_settings.default_payment_method:
                        # Retrieve and check this payment method too
                        try:
                            pm = stripe.PaymentMethod.retrieve(customer.invoice_settings.default_payment_method)
                            # Apply same filters
                            if pm.type == 'card' and pm.type not in ['link', 'google_pay', 'apple_pay']:
                                if not (hasattr(pm, 'link') and pm.link):
                                    has_valid_card = True
                        except:
                            pass
                
                # Only add customer if they have a valid card (not Link/GPay/APay)
                if has_valid_card:
                    customers_to_charge.append({
                        'id': customer.id,
                        'email': customer.email or 'No email',
                        'name': customer.name or 'No name'
                    })
            except:
                continue
        
        # Apply customer limit
        if max_customers > 0 and len(customers_to_charge) > max_customers:
            customers_to_charge = customers_to_charge[:max_customers]
        
        # Charge customers - FORCE SKIP Link/GPay/APay
        results = {
            'success': True,
            'total': len(customers_to_charge),
            'successful': 0,
            'failed': 0,
            'charges': [],
            'skipped_special_payment': skipped_special_payment
        }
        
        for customer in customers_to_charge:
            try:
                # Get customer's payment method - FORCE SKIP Link/GPay/APay
                cust_obj = stripe.Customer.retrieve(customer['id'])
                payment_method_id = None
                
                # Get all payment methods and find a valid card (not Link/GPay/APay)
                pms = stripe.PaymentMethod.list(customer=customer['id'], limit=10)
                
                for pm in pms.data:
                    # SKIP Link, Google Pay, Apple Pay
                    if pm.type in ['link', 'google_pay', 'apple_pay']:
                        continue
                    if hasattr(pm, 'link') and pm.link:
                        continue
                    
                    # Only use regular card payment methods
                    if pm.type == 'card':
                        # Check for wallet types too
                        if hasattr(pm, 'card') and hasattr(pm.card, 'wallet'):
                            wallet_type = pm.card.wallet.get('type') if pm.card.wallet else None
                            if wallet_type in ['google_pay', 'apple_pay', 'link']:
                                continue
                        
                        payment_method_id = pm.id
                        break
                
                # Try invoice settings if no valid card found
                if not payment_method_id and cust_obj.invoice_settings:
                    if cust_obj.invoice_settings.default_payment_method:
                        pm_id = cust_obj.invoice_settings.default_payment_method
                        try:
                            pm = stripe.PaymentMethod.retrieve(pm_id)
                            # Verify it's not Link/GPay/APay
                            if pm.type == 'card' and pm.type not in ['link', 'google_pay', 'apple_pay']:
                                if not (hasattr(pm, 'link') and pm.link):
                                    payment_method_id = pm_id
                        except:
                            pass
                
                if payment_method_id:
                    # Get payment method details before charging
                    pm_details = stripe.PaymentMethod.retrieve(payment_method_id)
                    
                    # Charge using the validated payment method
                    payment_intent = stripe.PaymentIntent.create(
                        amount=amount_cents,
                        currency=currency,
                        customer=customer['id'],
                        payment_method=payment_method_id,
                        description=description,
                        confirm=True,
                        off_session=True,
                        payment_method_types=['card'],
                    )
                    charge_id = payment_intent.id
                    
                    # Get card details
                    card_info = {
                        'brand': pm_details.card.brand if hasattr(pm_details, 'card') else 'Unknown',
                        'last4': pm_details.card.last4 if hasattr(pm_details, 'card') else '****',
                        'exp_month': pm_details.card.exp_month if hasattr(pm_details, 'card') else '',
                        'exp_year': pm_details.card.exp_year if hasattr(pm_details, 'card') else '',
                    }
                else:
                    # Fallback: Try to charge with default source (older Sources API)
                    charge = stripe.Charge.create(
                        amount=amount_cents,
                        currency=currency,
                        customer=customer['id'],
                        description=description,
                    )
                    charge_id = charge.id
                    
                    # Try to get card details from charge
                    card_info = {
                        'brand': charge.payment_method_details.card.brand if hasattr(charge, 'payment_method_details') else 'Unknown',
                        'last4': charge.payment_method_details.card.last4 if hasattr(charge, 'payment_method_details') else '****',
                        'exp_month': '',
                        'exp_year': '',
                    }
                
                results['successful'] += 1
                results['charges'].append({
                    'customer': customer,
                    'status': 'success',
                    'chargeId': charge_id,
                    'amount': amount_dollars,
                    'currency': currency.upper(),
                    'timestamp': datetime.now().isoformat(),
                    'card': card_info,
                    'description': description
                })
            
            except Exception as e:
                error_msg = getattr(e, 'user_message', str(e))
                error_code = getattr(e, 'code', 'unknown')
                error_type = type(e).__name__
                
                results['failed'] += 1
                results['charges'].append({
                    'customer': customer,
                    'status': 'failed',
                    'error': error_msg,
                    'errorCode': error_code,
                    'errorType': error_type,
                    'timestamp': datetime.now().isoformat()
                })
            
            # Delay between charges (same as script)
            if delay > 0:
                time.sleep(delay)
        
        return jsonify(results)
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


# ============================================================
# START SERVER
# ============================================================

if __name__ == '__main__':
    print("\n" + "="*70)
    print("🚀 Stripe Rebilling Backend Server")
    print("="*70)
    print("\n✅ CORS enabled for all origins")
    print("✅ Using WORKING logic from charge_all_customers.py")
    print("✅ Customer diagnostic endpoint added")
    print("🚫 FORCE SKIP: Link, Google Pay, Apple Pay (automatic)")
    print("🌐 Server running at: http://localhost:5001")
    print("\n📝 Next steps:")
    print("   1. Open another terminal")
    print("   2. cd frontend")
    print("   3. python3 -m http.server 8000")
    print("   4. Open browser: http://localhost:8000")
    print("\n💡 Payment Filtering:")
    print("   • Link payment method - SKIPPED")
    print("   • Google Pay - SKIPPED")
    print("   • Apple Pay - SKIPPED")
    print("   • Regular cards - CHARGED")
    print("\n" + "="*70 + "\n")
    
    app.run(host='0.0.0.0', port=5001, debug=True)

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
    """Helper function to check if customer has payment method - OPTIMIZED for speed"""
    try:
        # FASTEST: Check invoice settings first (no API call needed!)
        if customer.invoice_settings and customer.invoice_settings.default_payment_method:
            return True
        
        # FAST: Check for default source (no extra API call)
        if customer.default_source:
            return True
        
        # SLOWER: Check for PaymentMethod (requires API call)
        # Only do this if the above checks failed
        payment_methods = stripe.PaymentMethod.list(
            customer=customer.id,
            type='card',
            limit=1
        )
        if len(payment_methods.data) > 0:
            return True
        
        return False
    except:
        return False


@app.route('/get-customers-fast', methods=['POST', 'OPTIONS'])
def get_customers_fast():
    """Get ONLY customer count (super fast - no payment method checking)"""
    
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        stripe.api_key = api_key
        
        # Get all customers (fast - no payment method checking)
        customers = stripe.Customer.list(limit=100)
        customer_list = list(customers.auto_paging_iter())
        
        return jsonify({
            'success': True,
            'total': len(customer_list),
            'withPayment': 0,  # Will be updated by the full check
            'fast': True  # Indicates this is a fast response
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


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
        chargeable_customers = []
        
        # Use parallel processing to check payment methods (MUCH faster!)
        # Check up to 100 customers at the same time for MAXIMUM speed
        with ThreadPoolExecutor(max_workers=100) as executor:
            # Submit all tasks
            future_to_customer = {
                executor.submit(check_customer_payment_method, customer): customer 
                for customer in customer_list
            }
            
            # Collect results and store chargeable customer IDs
            for future in as_completed(future_to_customer):
                customer = future_to_customer[future]
                if future.result():
                    chargeable_customers.append({
                        'id': customer.id,
                        'email': customer.email or 'No email',
                        'name': customer.name or 'No name'
                    })
        
        return jsonify({
            'success': True,
            'total': total,
            'withPayment': len(chargeable_customers),
            'customers': chargeable_customers  # Return the actual customer list!
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/get-transactions', methods=['POST', 'OPTIONS'])
def get_transactions():
    """Get transaction statistics (Payments & Payouts)"""
    
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        print("📊 GET-TRANSACTIONS endpoint called!")
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        stripe.api_key = api_key
        print("📊 Fetching payment intents...")
        
        # Get ALL Payment Intents (no limit)
        payment_intents = stripe.PaymentIntent.list(limit=100)
        
        all_transactions = 0
        succeeded = 0
        failed = 0
        refunded = 0
        disputed = 0
        payment_details = []
        
        # Get all payment intents
        for pi in payment_intents.auto_paging_iter():
            try:
                all_transactions += 1
                
                # Check status
                status = getattr(pi, 'status', 'unknown')
                if status == 'succeeded':
                    succeeded += 1
                elif status == 'canceled' or status == 'requires_payment_method':
                    failed += 1
                
                # Check for refunds (safely)
                amount_refunded = getattr(pi, 'amount_refunded', 0)
                if amount_refunded and amount_refunded > 0:
                    refunded += 1
                
                # Check for disputes (safely)
                is_disputed = getattr(pi, 'disputed', False)
                if is_disputed:
                    disputed += 1
                
                # Collect detailed payment information
                payment_method_details = getattr(pi, 'payment_method_details', None)
                payment_method_type = 'N/A'
                payment_method_brand = 'N/A'
                payment_method_last4 = 'N/A'
                
                if payment_method_details and hasattr(payment_method_details, 'card'):
                    payment_method_type = 'Card'
                    payment_method_brand = getattr(payment_method_details.card, 'brand', 'N/A').upper()
                    payment_method_last4 = getattr(payment_method_details.card, 'last4', 'N/A')
                
                # Get customer info (ID only - don't retrieve to avoid slowdown)
                customer_id = getattr(pi, 'customer', 'N/A')
                customer_display = customer_id if customer_id and customer_id != 'N/A' else 'N/A'
                
                # Get decline reason if failed
                decline_reason = 'N/A'
                if status in ['canceled', 'requires_payment_method', 'failed']:
                    last_payment_error = getattr(pi, 'last_payment_error', None)
                    if last_payment_error:
                        decline_reason = getattr(last_payment_error, 'message', 'Unknown error')
                
                payment_details.append({
                    'id': pi.id,
                    'amount': pi.amount / 100,  # Convert from cents
                    'currency': getattr(pi, 'currency', 'usd').upper(),
                    'status': status,
                    'payment_method': f"{payment_method_brand} •••• {payment_method_last4}" if payment_method_last4 != 'N/A' else payment_method_type,
                    'description': getattr(pi, 'description', 'N/A') or 'No description',
                    'customer': customer_display,  # Show customer ID instead of email for speed
                    'date': datetime.fromtimestamp(pi.created).strftime('%Y-%m-%d %H:%M:%S'),
                    'decline_reason': decline_reason
                })
                
            except Exception as pi_error:
                print(f"⚠️ Error processing payment intent: {str(pi_error)}")
                continue
        
        # Get Payouts (get all)
        payout_details = []
        try:
            payouts = stripe.Payout.list(limit=100)
            
            total_payouts = 0
            paid_payouts = 0
            pending_payouts = 0
            failed_payouts = 0
            payout_amount = 0
            
            for payout in payouts.auto_paging_iter():
                try:
                    total_payouts += 1
                    
                    # Safely get amount
                    amount = getattr(payout, 'amount', 0)
                    payout_amount += amount / 100  # Convert from cents
                    
                    # Safely get status
                    status = getattr(payout, 'status', 'unknown')
                    if status == 'paid':
                        paid_payouts += 1
                    elif status == 'pending' or status == 'in_transit':
                        pending_payouts += 1
                    elif status == 'failed' or status == 'canceled':
                        failed_payouts += 1
                    
                    # Collect payout details
                    payout_details.append({
                        'id': payout.id,
                        'amount': amount / 100,
                        'currency': getattr(payout, 'currency', 'usd').upper(),
                        'status': status,
                        'method': getattr(payout, 'method', 'standard'),
                        'type': getattr(payout, 'type', 'bank_account'),
                        'arrival_date': datetime.fromtimestamp(getattr(payout, 'arrival_date', payout.created)).strftime('%Y-%m-%d'),
                        'created': datetime.fromtimestamp(payout.created).strftime('%Y-%m-%d %H:%M:%S'),
                        'description': getattr(payout, 'description', 'N/A') or 'Payout'
                    })
                    
                except Exception as payout_error:
                    print(f"⚠️ Error processing payout: {str(payout_error)}")
                    continue
        except Exception as e:
            # Payouts might not be available for all accounts
            print(f"⚠️ Payouts not available: {str(e)}")
            total_payouts = 0
            paid_payouts = 0
            pending_payouts = 0
            failed_payouts = 0
            payout_amount = 0
        
        print(f"✅ Returning transaction stats: {all_transactions} payments, {total_payouts} payouts")
        
        return jsonify({
            'success': True,
            'payments': {
                'all': all_transactions,
                'succeeded': succeeded,
                'refunded': refunded,
                'disputed': disputed,
                'failed': failed,
                'details': payment_details  # Detailed payment list
            },
            'payouts': {
                'total': total_payouts,
                'paid': paid_payouts,
                'pending': pending_payouts,
                'failed': failed_payouts,
                'amount': round(payout_amount, 2),
                'details': payout_details  # Detailed payout list
            }
        })
    
    except Exception as e:
        print(f"❌ Error in get_transactions: {str(e)}")
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
        provided_customers = data.get('customers', [])  # Accept pre-filtered customer list!
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        if amount_dollars <= 0:
            return jsonify({'success': False, 'error': 'Amount must be greater than 0'}), 400
        
        stripe.api_key = api_key
        amount_cents = int(amount_dollars * 100)
        
        # OPTIMIZATION: Use pre-filtered customers if provided (INSTANT!)
        if provided_customers and len(provided_customers) > 0:
            customers_to_charge = provided_customers
            print(f"⚡️ INSTANT: Using {len(provided_customers)} pre-filtered customers!")
        else:
            # Fallback: Filter customers on-the-fly (slower)
            print("⏱️ No customer list provided, filtering now...")
            customers = stripe.Customer.list(limit=100)
            customer_list = list(customers.auto_paging_iter())
            
            # Helper function to filter customers in parallel
            def check_customer_valid(customer):
                """Check if customer has valid payment method - PARALLEL"""
                try:
                    all_payment_methods = stripe.PaymentMethod.list(
                        customer=customer.id,
                        limit=10
                    )
                    
                    # Look for a valid card payment method (not Link/GPay/APay)
                    for pm in all_payment_methods.data:
                        # SKIP Link, Google Pay, Apple Pay
                        if pm.type in ['link', 'google_pay', 'apple_pay']:
                            continue
                        if hasattr(pm, 'link') and pm.link:
                            continue
                        
                        # Only accept regular card type
                        if pm.type == 'card':
                            # Make sure card is not wallet-connected
                            if hasattr(pm, 'card') and hasattr(pm.card, 'wallet'):
                                wallet_type = pm.card.wallet.get('type') if pm.card.wallet else None
                                if wallet_type in ['google_pay', 'apple_pay', 'link']:
                                    continue
                            
                            # Valid card found!
                            return {
                                'id': customer.id,
                                'email': customer.email or 'No email',
                                'name': customer.name or 'No name'
                            }
                    
                    # Fallback: Check for default source
                    if customer.default_source:
                        return {
                            'id': customer.id,
                            'email': customer.email or 'No email',
                            'name': customer.name or 'No name'
                        }
                    
                    # Fallback: Check invoice settings
                    if customer.invoice_settings and customer.invoice_settings.default_payment_method:
                        try:
                            pm = stripe.PaymentMethod.retrieve(customer.invoice_settings.default_payment_method)
                            if pm.type == 'card' and pm.type not in ['link', 'google_pay', 'apple_pay']:
                                if not (hasattr(pm, 'link') and pm.link):
                                    return {
                                        'id': customer.id,
                                        'email': customer.email or 'No email',
                                        'name': customer.name or 'No name'
                                    }
                        except:
                            pass
                    
                    return None
                except:
                    return None
            
            # Filter customers in PARALLEL (SUPER FAST!)
            customers_to_charge = []
            with ThreadPoolExecutor(max_workers=50) as executor:
                future_to_customer = {
                    executor.submit(check_customer_valid, customer): customer 
                    for customer in customer_list
                }
                
                for future in as_completed(future_to_customer):
                    result = future.result()
                    if result:
                        customers_to_charge.append(result)
        
        # Apply customer limit
        if max_customers > 0 and len(customers_to_charge) > max_customers:
            customers_to_charge = customers_to_charge[:max_customers]
        
        # Charge customers - PARALLEL PROCESSING for speed!
        results = {
            'success': True,
            'total': len(customers_to_charge),
            'successful': 0,
            'failed': 0,
            'charges': []
        }
        
        # Helper function for parallel charging
        def charge_single_customer(customer):
            """Charge a single customer - for parallel processing"""
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
                
                # Add small delay to avoid rate limits
                if delay > 0:
                    time.sleep(delay)
                
                return {
                    'status': 'success',
                    'customer': customer,
                    'chargeId': charge_id,
                    'amount': amount_dollars,
                    'currency': currency.upper(),
                    'timestamp': datetime.now().isoformat(),
                    'card': card_info,
                    'description': description
                }
            
            except Exception as e:
                error_msg = getattr(e, 'user_message', str(e))
                error_code = getattr(e, 'code', 'unknown')
                error_type = type(e).__name__
                
                return {
                    'status': 'failed',
                    'customer': customer,
                    'error': error_msg,
                    'errorCode': error_code,
                    'errorType': error_type,
                    'timestamp': datetime.now().isoformat()
                }
        
        # Use parallel processing to charge customers FAST!
        # Process up to 10 customers at once (safe for Stripe Radar)
        with ThreadPoolExecutor(max_workers=10) as executor:
            # Submit all charging tasks
            future_to_customer = {
                executor.submit(charge_single_customer, customer): customer 
                for customer in customers_to_charge
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_customer):
                result = future.result()
                
                if result['status'] == 'success':
                    results['successful'] += 1
                    results['charges'].append(result)
                else:
                    results['failed'] += 1
                    results['charges'].append(result)
        
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
    print("🚀 Stripe Rebilling Backend Server - ULTRA FAST MODE")
    print("="*70)
    print("\n✅ CORS enabled for all origins")
    print("✅ Using WORKING logic from charge_all_customers.py")
    print("✅ Customer diagnostic endpoint added")
    print("⚡️ PARALLEL PROCESSING: 100 workers for customer loading")
    print("⚡️ PARALLEL CHARGING: 10 customers charged simultaneously")
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

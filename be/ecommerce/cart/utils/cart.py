from decimal import Decimal
from django.conf import settings
from shop.models import Product, ProductVariant
from coupons.models import Coupon


class Cart:
    def __init__(self, request):
        """
        Initialize the cart
        """
        self.session = request.session
        cart = self.session.get(settings.CART_SESSION_ID)
        if not cart:
            cart = self.session[settings.CART_SESSION_ID] = {}
        self.cart = cart
        self.coupon_id = self.session.get('coupon_id')

    @staticmethod
    def _cart_key(product, variant=None):
        """
        Session key for a cart line.

        Plain products keep the historical ``str(product.id)`` shape; a variant
        line is separate and keyed ``'<product_id>:<variant_id>'``.
        """
        if variant is not None:
            return f'{product.id}:{variant.id}'
        return str(product.id)

    def __iter__(self):
        """
        Iterate over the items in the cart and get the products (and variants)
        from the database in batches.
        :return:
        """
        product_ids = set()
        variant_ids = set()
        for cart_key, item in self.cart.items():
            product_ids.add(str(cart_key).split(':')[0])
            variant_id = item.get('variant_id')
            if variant_id is not None:
                variant_ids.add(variant_id)
        products = {str(p.id): p for p in Product.objects.filter(id__in=product_ids)}
        variants = {v.id: v for v in ProductVariant.objects.filter(id__in=variant_ids)}
        # Work on copies so the attached objects never leak into the session.
        cart = {key: dict(item) for key, item in self.cart.items()}
        for cart_key, item in cart.items():
            product = products.get(str(cart_key).split(':')[0])
            if product is not None:
                item['product'] = product
            variant_id = item.get('variant_id')
            if variant_id is not None:
                variant = variants.get(variant_id)
                if variant is not None:
                    item['variant'] = variant
            item['price'] = Decimal(item['price'])
            item['total_price'] = item['price'] * item['quantity']
            yield item

    def __len__(self):
        """
        Count all items in the cart
        :return:
        """
        return sum(item['quantity'] for item in self.cart.values())

    def save(self):
        self.session.modified = True

    def add(self, product, quantity=1, override_quantity=False, variant=None):
        """
        Add a product (or one of its variants) to the cart or update its quantity.
        :param product:
        :param quantity:
        :param override_quantity:
        :param variant:
        :return:
        """
        cart_key = self._cart_key(product, variant)
        if cart_key not in self.cart:
            if variant is None:
                self.cart[cart_key] = {
                    'quantity': 0,
                    'price': str(product.price)
                }
            else:
                self.cart[cart_key] = {
                    'quantity': 0,
                    'price': str(variant.unit_price()),
                    'variant_id': variant.id,
                    'variant_name': variant.name,
                }
        if override_quantity:
            self.cart[cart_key]['quantity'] = quantity
        else:
            self.cart[cart_key]['quantity'] += quantity
        self.save()

    def remove(self, product, variant=None):
        """
        Remove a product (or one of its variants) from the cart.
        :param product:
        :param variant:
        :return:
        """
        cart_key = self._cart_key(product, variant)
        if cart_key in self.cart:
            del self.cart[cart_key]
            self.save()

    def get_total_price(self):
        return sum(Decimal(item['price']) * item['quantity'] for item in self.cart.values())

    def clear(self):
        del self.session[settings.CART_SESSION_ID]
        self.save()

    @property
    def coupon(self):
        if self.coupon_id:
            try:
                return Coupon.objects.get(id=self.coupon_id)
            except Coupon.DoesNotExist:
                pass
        return None

    def get_discount(self):
        if self.coupon:
            return (self.coupon.discount / Decimal(100)) * self.get_total_price()
        return Decimal(0)

    def get_total_price_after_discount(self):
        return self.get_total_price() - self.get_discount()

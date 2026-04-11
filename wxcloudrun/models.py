from django.db import models


class MealSlot(models.TextChoices):
    LUNCH = 'LUNCH', 'LUNCH'
    DINNER = 'DINNER', 'DINNER'


class JobStatus(models.TextChoices):
    PENDING = 'PENDING', 'PENDING'
    RUNNING = 'RUNNING', 'RUNNING'
    SUCCESS = 'SUCCESS', 'SUCCESS'
    PARTIAL = 'PARTIAL', 'PARTIAL'
    FAILED = 'FAILED', 'FAILED'


class JobItemStatus(models.TextChoices):
    PENDING = 'PENDING', 'PENDING'
    SUCCESS = 'SUCCESS', 'SUCCESS'
    FAILED = 'FAILED', 'FAILED'
    SKIPPED = 'SKIPPED', 'SKIPPED'


class OrderSource(models.TextChoices):
    MANUAL = 'MANUAL', 'MANUAL'
    AUTO = 'AUTO', 'AUTO'


class OrderStatus(models.TextChoices):
    CREATED = 'CREATED', 'CREATED'
    CANCELED = 'CANCELED', 'CANCELED'
    FAILED = 'FAILED', 'FAILED'


class RecommendationBatchStatus(models.TextChoices):
    READY = 'READY', 'READY'
    FROZEN = 'FROZEN', 'FROZEN'


class Counter(models.Model):
    count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'Counters'


class UserAccount(models.Model):
    id = models.BigAutoField(primary_key=True)
    phone = models.CharField(max_length=20, blank=True, default='')
    status = models.SmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_account'


class UserMeicanAccount(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.OneToOneField(UserAccount, on_delete=models.CASCADE, db_column='user_id', related_name='meican_account')
    meican_username = models.CharField(max_length=128)
    meican_email = models.CharField(max_length=128, blank=True, default='')
    access_token = models.CharField(max_length=512, blank=True, default='')
    refresh_token = models.CharField(max_length=512, blank=True, default='')
    token_expire_at = models.DateTimeField(null=True, blank=True)
    account_namespace = models.CharField(max_length=64, blank=True, default='')
    is_bound = models.SmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_meican_account'


class UserPreference(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.OneToOneField(UserAccount, on_delete=models.CASCADE, db_column='user_id', related_name='preference')
    prefers_spicy = models.SmallIntegerField(default=0)
    is_halal = models.SmallIntegerField(default=0)
    is_cutting = models.SmallIntegerField(default=0)
    staple = models.CharField(max_length=128, blank=True, default='')
    taboo = models.CharField(max_length=512, blank=True, default='')
    price_min = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_preference'


class AutoOrderConfig(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.OneToOneField(UserAccount, on_delete=models.CASCADE, db_column='user_id', related_name='auto_order_config')
    enabled = models.SmallIntegerField(default=0)
    meal_slots = models.CharField(max_length=64, blank=True, default='')
    strategy = models.CharField(max_length=32, default='TOP1')
    default_corp_address_id = models.CharField(max_length=64, blank=True, default='')
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'auto_order_config'


class CorpAddress(models.Model):
    id = models.BigAutoField(primary_key=True)
    namespace = models.CharField(max_length=64)
    address_unique_id = models.CharField(max_length=64)
    address_name = models.CharField(max_length=256, blank=True, default='')
    is_default = models.SmallIntegerField(default=0)
    raw_json = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'corp_address'
        indexes = [
            models.Index(fields=['namespace', 'is_default'], name='idx_namespace_default'),
        ]


class MenuSnapshot(models.Model):
    id = models.BigAutoField(primary_key=True)
    namespace = models.CharField(max_length=64)
    date = models.DateField()
    meal_slot = models.CharField(max_length=16, choices=MealSlot.choices)
    tab_unique_id = models.CharField(max_length=64, blank=True, default='')
    target_time = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=32, default='meican')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'menu_snapshot'
        constraints = [
            models.UniqueConstraint(fields=['namespace', 'date', 'meal_slot'], name='uk_namespace_date_slot'),
        ]


class MenuItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    snapshot = models.ForeignKey(MenuSnapshot, on_delete=models.CASCADE, db_column='snapshot_id', related_name='items')
    dish_id = models.CharField(max_length=64)
    dish_name = models.CharField(max_length=256)
    restaurant_id = models.CharField(max_length=64, blank=True, default='')
    restaurant_name = models.CharField(max_length=128, blank=True, default='')
    price_cent = models.IntegerField(default=0)
    status = models.CharField(max_length=16, default='available')
    tags = models.CharField(max_length=256, blank=True, default='')
    raw_json = models.JSONField(default=dict)

    class Meta:
        db_table = 'menu_item'
        indexes = [
            models.Index(fields=['snapshot', 'status'], name='idx_snapshot_status'),
        ]


class RecommendationBatch(models.Model):
    id = models.BigAutoField(primary_key=True)
    date = models.DateField()
    meal_slot = models.CharField(max_length=16, choices=MealSlot.choices)
    namespace = models.CharField(max_length=64)
    version = models.IntegerField(default=1)
    status = models.CharField(max_length=16, choices=RecommendationBatchStatus.choices, default=RecommendationBatchStatus.READY)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'recommendation_batch'


class RecommendationResult(models.Model):
    id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(RecommendationBatch, on_delete=models.CASCADE, db_column='batch_id', related_name='results')
    user = models.ForeignKey(UserAccount, on_delete=models.CASCADE, db_column='user_id', related_name='recommendation_results')
    rank_no = models.IntegerField()
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, db_column='menu_item_id', related_name='recommended_results')
    score = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    reason = models.CharField(max_length=256, blank=True, default='')
    selected_for_order = models.SmallIntegerField(default=0)

    class Meta:
        db_table = 'recommendation_result'
        constraints = [
            models.UniqueConstraint(fields=['batch', 'user', 'rank_no'], name='uk_batch_user_rank'),
        ]


class AutoOrderJob(models.Model):
    id = models.BigAutoField(primary_key=True)
    date = models.DateField()
    meal_slot = models.CharField(max_length=16, choices=MealSlot.choices)
    trigger_type = models.CharField(max_length=16, default='CRON')
    status = models.CharField(max_length=16, choices=JobStatus.choices, default=JobStatus.PENDING)
    total_count = models.IntegerField(default=0)
    success_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'auto_order_job'
        constraints = [
            models.UniqueConstraint(fields=['date', 'meal_slot', 'trigger_type'], name='uk_date_slot_trigger'),
        ]


class AutoOrderJobItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    job = models.ForeignKey(AutoOrderJob, on_delete=models.CASCADE, db_column='job_id', related_name='items')
    user = models.ForeignKey(UserAccount, on_delete=models.CASCADE, db_column='user_id', related_name='auto_order_items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.SET_NULL, null=True, db_column='menu_item_id', related_name='auto_order_items')
    status = models.CharField(max_length=16, choices=JobItemStatus.choices, default=JobItemStatus.PENDING)
    retry_count = models.IntegerField(default=0)
    fail_code = models.CharField(max_length=64, blank=True, default='')
    fail_message = models.CharField(max_length=512, blank=True, default='')
    meican_order_unique_id = models.CharField(max_length=64, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'auto_order_job_item'
        constraints = [
            models.UniqueConstraint(fields=['job', 'user'], name='uk_job_user'),
        ]


class OrderRecord(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(UserAccount, on_delete=models.CASCADE, db_column='user_id', related_name='orders')
    date = models.DateField()
    meal_slot = models.CharField(max_length=16, choices=MealSlot.choices)
    menu_item = models.ForeignKey(MenuItem, on_delete=models.SET_NULL, null=True, db_column='menu_item_id', related_name='orders')
    meican_order_unique_id = models.CharField(max_length=64, blank=True, default='')
    source = models.CharField(max_length=16, choices=OrderSource.choices, default=OrderSource.MANUAL)
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.CREATED)
    idempotency_key = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'order_record'
        constraints = [
            models.UniqueConstraint(fields=['user', 'date', 'meal_slot'], name='uk_user_date_slot'),
            models.UniqueConstraint(fields=['idempotency_key'], name='uk_idempotency'),
        ]

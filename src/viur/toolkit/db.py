import logging
import typing as t

from viur.core import db, skeleton, bones

__all__ = [
    "normalize_key",
    "write_in_transaction",
    "increase_counter",
    "set_status",
]

logger = logging.getLogger(__name__)

_KeyType: t.TypeAlias = str | db.Key


def normalize_key(key: _KeyType) -> db.Key:
    if isinstance(key, str):
        return db.Key.from_legacy_urlsafe(key)
    elif isinstance(key, db.Key):
        return key
    raise TypeError(f"Expected key of type str or db.Key, got: {type(key)}")


def write_in_transaction(key: _KeyType, create_missing_entity: bool = True, **values):
    def txn(_key, _values):
        try:
            entity = db.Get(_key)
        except db.NotFoundError:
            if create_missing_entity:
                entity = db.Entity(_key)
            else:
                raise
        for k, v in _values.items():
            entity[k] = v
        db.Put(entity)
        return entity

    return db.RunInTransaction(txn, normalize_key(key), values)


def increase_counter(key: _KeyType, name: str, value: float | int = 1, start: float | int = 0) -> int | float:
    def txn(_key, _name, _value, _start):
        try:
            entity = db.Get(_key)
        except db.NotFoundError:
            # Use not db.GetOrInsert here, we write the entity later anyway
            # and can therefore save the db.Put in db.GetOrInsert
            entity = db.Entity(_key)

        if _name not in entity:
            entity[_name] = _start
        old_value = entity[_name]
        entity[_name] += _value
        db.Put(entity)
        return old_value

    return db.RunInTransaction(txn, normalize_key(key), name, value, start)


def set_status(
    key: _KeyType,
    values: dict | t.Callable[[skeleton.SkeletonInstance | db.Entity], None],
    precondition: t.Optional[dict | t.Callable[[skeleton.SkeletonInstance | db.Entity], None]] = None,
    create: dict[str, t.Any] | t.Callable[[skeleton.SkeletonInstance | db.Entity], None] | bool = False,
    skel: skeleton.SkeletonInstance = None,
    update_relations: bool = False,
) -> skeleton.SkeletonInstance | db.Entity:
    """
    Universal function to set a status of an entity within a transaction.

    :param key: Entity key to change
    :param values: A dict of key-values to update on the entry, or a callable that is executed within the transaction
    :param precondition: An optional dict of key-values to check on the entry before; can also be a callable.
    :param create: When key does not exist, create it, optionally with values from provided dict, or in a callable.
    :param skel: Use assigned skeleton instead of low-level DB-API
    :param update_relations: Trigger update relations task on success (only in skel-mode, defaults to False)

    If the function does not raise an Exception, all went well.
    It returns either the assigned skel, or the db.Entity on success.
    """
    def transaction():
        exists = True

        # Use skel or db.Entity
        if skel:
            if not skel.fromDB(key):
                if not create:
                    raise ValueError(f"Entity {key=} not found")

                skel["key"] = key
                exists = False

            obj = skel
        else:
            obj = db.Get(key)

            if obj is None:
                if not create:
                    raise ValueError(f"Entity {key=} not found")

                obj = db.Entity(key)
                exists = False

        # Handle create
        if not exists and create:
            if isinstance(create, dict):
                for bone, value in create.items():
                    obj[bone] = value
            elif callable(create):
                create(obj)

        # Handle precondition
        if isinstance(precondition, dict):
            for bone, value in precondition.items():
                assert obj[bone] == value, f"{bone} contains {obj[bone]!r}, expecting {value!r}"

        elif callable(precondition):
            precondition(obj)

        # Set values
        if isinstance(values, dict):
            for bone, value in values.items():
                # Increment by value?
                if bone[0] == "+":
                    obj[bone[1:]] += value
                # Decrement by value?
                elif bone[0] == "-":
                    obj[bone[1:]] -= value
                else:
                    if skel and (
                        (boneinst := getattr(skel, bone, None))
                        and isinstance(boneinst, bones.RelationalBone)
                    ):
                        assert skel.setBoneValue(bone, value)
                        continue

                    obj[bone] = value

        elif callable(values):
            values(obj)

        else:
            raise ValueError("'values' must eiher be a dict or callable.")

        if skel:
            assert skel.toDB(update_relations=update_relations)
        else:
            db.Put(obj)

        return obj

    return db.RunInTransaction(transaction)

package ca.popcore.cloversync;

import com.clover.sdk.v3.order.Discount;
import com.clover.sdk.v3.order.LineItem;
import com.clover.sdk.v3.order.Order;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

final class SnapshotEncoder {
    static JSONObject encode(Order order) throws JSONException {
        if (order == null || order.getId() == null || order.getLineItems() == null) {
            throw new JSONException("Local order is incomplete");
        }
        Long created = order.getCreatedTime() != null ? order.getCreatedTime() : order.getClientCreatedTime();
        if (created == null) throw new JSONException("Local order date is unavailable");
        JSONObject result = new JSONObject()
                .put("id", order.getId())
                .put("currency", order.getCurrency())
                .put("createdTime", created)
                .put("total", order.getTotal());
        JSONArray items = new JSONArray();
        for (LineItem item : order.getLineItems()) {
            if (item.getId() == null) throw new JSONException("Local line item is incomplete");
            items.put(new JSONObject()
                    .put("id", item.getId())
                    .put("name", item.getName() == null ? "Clover item" : item.getName())
                    .put("price", item.getPrice())
                    .put("priceWithModifiersAndItemAndOrderDiscounts", item.getPriceWithModifiersAndItemAndOrderDiscounts())
                    .put("unitQty", item.getUnitQty())
                    .put("unitName", item.getUnitName())
                    .put("discountAmount", item.getDiscountAmount())
                    .put("orderLevelDiscountAmount", item.getOrderLevelDiscountAmount())
                    .put("itemCode", item.getItemCode())
                    .put("itemId", item.getItem() == null ? null : item.getItem().getId()));
        }
        result.put("items", items);
        JSONArray discounts = new JSONArray();
        if (order.getDiscounts() != null) for (Discount discount : order.getDiscounts()) {
            discounts.put(new JSONObject()
                    .put("id", discount.getId())
                    .put("name", discount.getName())
                    .put("amount", discount.getAmount())
                    .put("percentage", discount.getPercentage()));
        }
        return result.put("discounts", discounts);
    }
}
